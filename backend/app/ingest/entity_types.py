"""Derive an entity-type taxonomy from the wiki corpus.

An entity type is a category of real-world thing the wiki tracks — an organization, a
person, a software product. Anything that keys facts by entity needs such a taxonomy, and
the obvious way to get one is for someone to write it down. That does not survive contact
with customers: nobody arriving with a wiki is going to author a type list first, and a list
written for one deployment is wrong for the next.

So derive it from the pages themselves. The pipeline reads only the wiki — never an
incoming document — because a taxonomy has to describe the corpus it will be applied to:

    1. EXTRACT   per page, whole page, with NO type menu. Supplying one would presuppose the
                 answer; we want the referents the corpus actually contains.
    2. FOLD      merge spellings of one thing (case, punctuation, legal suffix, version).
                 LEXICAL, not semantic — see fold() for why embeddings fail here.
    3. DROP      corpus artifacts: page titles and code symbols are things the wiki is MADE
                 OF, not things it tracks.
    4. GROUP     cluster referents by kind — this one IS semantic.
    5. NAME      one call per group: name the kind from its observed instances.
    6. MERGE     one call over the whole taxonomy; per-group naming cannot generalise.

Note what is NOT filtered: nothing is excluded for being too common or too rare, at either
the referent or the type level. All three were tried and all three were wrong. Excluding
ubiquitous referents starved the taxonomy of real members — a tool named on every page is
still a tool. Excluding single sightings answered "is this entity real?" when the question
here is "what kinds exist?". And a minimum-members floor on the types themselves was a
no-op whenever the merge worked, while replacing true statements with a bucket: a `font`
type with two members says something, `other` says nothing.

Keeping types few and general is the MERGE step's job, and it is effective at it — the
per-group naming step over-splits by construction, and one pass over the whole taxonomy
collapses that. A second, count-based implementation of the same intent only cost
information.

Deliberately NOT solved here: exact entity resolution to canonical ids. Types need
approximate counts, not identities — and once types exist, resolution gets easier because
it runs WITHIN a type rather than over all pairs.

The result is a frozen, versioned artifact. It must not be recomputed silently: stages 5 and
6 are LLM calls, so re-deriving can rename a type, and anything keyed by the old name is
orphaned. ``derive()`` therefore only computes — ``run_derivation()`` is the entry point a
caller invokes (today the offline queue task; see ``app.tasks.entity_types``), and it is not
scheduled. When nothing has been derived yet, ``load_taxonomy`` falls back to a small generic
type list, the same degradation as the relevance scorer without its model file.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, Field

from app.db import entity_taxonomy
from app.llm import client, embeddings
from app.llm.prompts import load_prompt
from app.wiki import filesystem, git as wiki_git

log = logging.getLogger(__name__)

# --- parameters ---------------------------------------------------------------------------
# Cosine floor for "same kind of thing" — the one parameter with real leverage, because it
# sets how many groups the naming step sees and therefore what it can generalise from.
#
# Both extremes fail, in opposite ways:
#   too low   a few enormous groups. Naming is asked what kind hundreds of unrelated
#             referents share and produces something vacuous. Looks like a prompt problem.
#   too high  mostly singletons. Naming has ONE example to generalise from, so it emits a
#             hyper-specific type per referent and the merge step has to undo all of it.
# Aim for groups of roughly ten: enough examples to see a kind, few enough to be one kind.
#
# Retune it if the clustering algorithm changes. The value is specific to leader clustering,
# which compares a candidate against a running centroid — that centroid drifts toward a
# generic average as a group grows and then admits almost anything, so it tolerates far less
# permissiveness than a linkage-based method at the "same" threshold.
GROUP_SIMILARITY = 0.45
MERGE_ROUNDS = 3  # merge exits on convergence; this only bounds the loop

# Fallback when no derived artifact is present, so a deployment that has never run the
# derivation still has something usable. Intentionally generic: these are the types that
# recur across corpora, not a customer's.
DEFAULT_TYPES: dict[str, str] = {
    "organization": "A named company, customer, vendor, institution, or other collective body.",
    "person": "An individual human being identified by a personal name.",
    "software_product_or_service": (
        "A named software application, service, platform, or tool used to perform a function."
    ),
    "protocol_or_standard": (
        "A named technical protocol, standard, or format specifying how systems interoperate."
    ),
}

# --- lexical folding --------------------------------------------------------------------
# LEXICAL means the comparison looks only at the CHARACTERS — case, punctuation, whitespace,
# token order, suffixes. Not at meaning. The variants we need to merge differ only in form
# while denoting the same thing:
#
#     "ACME" / "Acme"                 case
#     "ACME&CO" / "Acme & Co"         punctuation and spacing
#     "Acme AB" / "Acme"              legal suffix
#     "Acme v4" / "Acme"              version marker
#
# Every one of those is recoverable by normalising the string. None of them requires knowing
# what Acme is.
#
# An embedding, by contrast, compares MEANING, and that is the wrong axis. Two competing
# products in one category are semantically close while denoting different things; a product
# and its own sub-brand can be semantically further apart than that pair, because the
# sub-brand adds a distinguishing word. So the "same thing, different spelling" band and the
# "different thing, similar meaning" band overlap, and no similarity cut separates them —
# it either misses variants or merges competitors.
#
# Containment is deliberately NOT used either. One name being a prefix of another does not
# make them the same thing: a vendor's name usually prefixes its products' names, so a
# containment rule collapses vendors into their product lines.
LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "ltd",
        "llc",
        "plc",
        "ab",
        "gmbh",
        "corp",
        "corporation",
        "co",
        "sa",
        "nv",
        "bv",
        "oy",
        "as",
        "aps",
        "spa",
        "srl",
        "pty",
        "limited",
        "holdings",
    }
)
_VERSION_TOKEN = re.compile(r"^v?\d+(?:\.\d+)*$")
_TOKENS = re.compile(r"[^A-Za-z0-9]+")


def normalize_surface(surface: str) -> str:
    """Lexical key for variant folding. Deterministic, no model involved.

    Tokenises on non-alphanumerics, then strips trailing legal suffixes and version markers,
    so "Acme AB" -> "acme" and "Acme v4" -> "acme", while "Acme Teams" keeps both tokens —
    "Teams" is not a suffix, so a vendor is not collapsed into its product.
    """
    tokens = [t for t in _TOKENS.split(surface.lower()) if t]
    while tokens and (tokens[-1] in LEGAL_SUFFIXES or _VERSION_TOKEN.match(tokens[-1])):
        tokens.pop()
    return " ".join(tokens) or surface.strip().lower()


def is_corpus_artifact(surface: str, page_titles: set[str]) -> str:
    """Is this a thing the corpus TRACKS, or a thing the corpus is MADE OF?

    Page headings and code symbols leak past the extraction prompt and then get typed as
    real kinds ("wiki_page", "task_queue"). They are artifacts of the medium, not referents
    in the world, so they are dropped by rule rather than argued with.
    """
    name = surface.strip()
    if name.lower() in page_titles:
        return "page_title"
    # snake_case, no spaces, all lower -> a code symbol (ingest_selector, documents_queue).
    # Deliberately narrow: leaves Next.js, @scope/pkg and react-native-mmkv alone.
    if "_" in name and " " not in name and name == name.lower():
        return "code_identifier"
    return ""


# --- records ----------------------------------------------------------------------------
class Mention(BaseModel):
    """One referent as a single page named it."""

    surface: str
    page: str
    role: str = ""


class Referent(BaseModel):
    """Folded surface variants — one approximate entity."""

    canonical: str
    variants: list[str] = Field(default_factory=list)
    pages: set[str] = Field(default_factory=set)
    roles: list[str] = Field(default_factory=list)

    @property
    def n_docs(self) -> int:
        return len(self.pages)


class EntityType(BaseModel):
    """A derived type, carrying the evidence for its own existence."""

    name: str
    definition: str
    examples: list[str] = Field(default_factory=list)
    n_referents: int = 0
    n_docs: int = 0


# --- vector helpers (no numpy in the backend) -------------------------------------------
def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    """Both operands are unit vectors, so the dot product IS the cosine."""
    return sum(x * y for x, y in zip(a, b))


def _leader_cluster(
    vectors: list[list[float]], order: list[int], threshold: float
) -> list[list[int]]:
    """Greedy leader clustering: each item joins the first cluster whose centroid is within
    ``threshold``, else seeds a new one.

    Chosen over agglomerative linkage because the backend has no scipy/sklearn and an O(n^3)
    pure-Python linkage would not finish on a real corpus. ``order`` should put the
    best-supported referents first so they seed clusters — that makes the result
    deterministic and puts the strongest evidence in charge of each group.
    """
    clusters: list[list[int]] = []
    centroids: list[list[float]] = []
    for idx in order:
        vec = vectors[idx]
        best, best_sim = -1, threshold
        for c, centroid in enumerate(centroids):
            sim = _cosine(vec, centroid)
            if sim >= best_sim:
                best, best_sim = c, sim
        if best < 0:
            clusters.append([idx])
            centroids.append(list(vec))
            continue
        members = clusters[best]
        centroid = centroids[best]
        n = len(members)
        centroids[best] = _normalize([(c * n + v) / (n + 1) for c, v in zip(centroid, vec)])
        members.append(idx)
    return clusters


# --- LLM steps --------------------------------------------------------------------------
def _complete_json(system: str, user: str, *, model: str | None) -> dict[str, Any] | None:
    """One completion parsed as a JSON object. Returns None rather than raising — a single
    failed page or group must not abort a corpus-wide derivation."""
    try:
        result = client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
        )
    except Exception:
        log.warning("entity_types: completion failed", exc_info=True)
        return None
    text = (result.text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = cast(object, json.loads(text[start : end + 1]))
    except json.JSONDecodeError:
        log.warning("entity_types: unparseable JSON (%d chars)", len(text))
        return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None


def extract_page(path: str, body: str, *, model: str | None = None) -> list[Mention]:
    """Open extraction over one whole page. No type menu — see module docstring."""
    system = load_prompt("entity_types.extract")
    data = _complete_json(system, f"path:\n{path}\ncontent:\n{body}", model=model)
    if not data or not isinstance(data.get("referents"), list):
        return []
    out: list[Mention] = []
    for raw_item in cast(list[Any], data["referents"]):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, Any], raw_item)
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 120:
            continue
        out.append(Mention(surface=name, page=path, role=str(item.get("what") or "").strip()[:160]))
    return out


def _member_indices(entry: dict[str, Any], upper: int) -> list[int]:
    """Zero-based member indices from an LLM payload, dropping anything out of range."""
    raw = entry.get("member_indices")
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in cast(list[Any], raw):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        index = int(value)
        if 1 <= index <= upper:
            out.append(index - 1)
    return out


def name_group(group: list[Referent], *, model: str | None = None) -> list[EntityType]:
    """Name the kind a group shares. May split a group that turns out to be mixed."""
    system = load_prompt("entity_types.name")
    listing = "\n".join(
        f"[{i}] {r.canonical}"
        + (f" -- {r.roles[0]}" if r.roles else "")
        + f"  (on {r.n_docs} page{'s' if r.n_docs != 1 else ''})"
        for i, r in enumerate(group, start=1)
    )
    data = _complete_json(system, f"Referents in this group:\n\n{listing}", model=model)

    # The prompt requires a partition: every member in exactly one type. Enforce it. A
    # response that omits members would silently drop referents, and one that repeats them
    # would inflate the support counts a type is judged on — so a partial answer is treated
    # as no answer, not as a smaller one.
    out: list[EntityType] = []
    claimed: set[int] = set()
    for raw_entry in cast(list[Any], (data or {}).get("types") or []):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, Any], raw_entry)
        name = str(entry.get("type_name") or "").strip().lower().replace(" ", "_")
        indices = _member_indices(entry, len(group))
        if not name or not indices:
            continue
        if claimed & set(indices):
            log.warning("entity_types: naming returned overlapping members; ignoring response")
            out = []
            break
        claimed.update(indices)
        members = [group[i] for i in indices]
        out.append(
            EntityType(
                name=name,
                definition=str(entry.get("definition") or "").strip(),
                examples=[r.canonical for r in members[:8]],
                n_referents=len(members),
                n_docs=len({p for r in members for p in r.pages}),
            )
        )
    if out and len(claimed) < len(group):
        log.warning(
            "entity_types: naming covered %d of %d member(s); ignoring response",
            len(claimed),
            len(group),
        )
        out = []
    if out:
        return out
    # A group we could not name is still evidence; keep it visible rather than dropping it.
    return [
        EntityType(
            name=f"unnamed_{len(group)}",
            definition="(naming failed)",
            examples=[r.canonical for r in group[:8]],
            n_referents=len(group),
            n_docs=len({p for r in group for p in r.pages}),
        )
    ]


def merge_types(types: list[EntityType], *, model: str | None = None) -> list[EntityType]:
    """Collapse the over-splitting per-group naming produces.

    Naming is local — a call looking at three medical companies cannot know a hundred other
    organizations exist elsewhere, so it emits ``medical_organization``. This is the only
    stage that sees the whole taxonomy. Iterates to convergence: one pass over dozens of
    types must partition every index without slip, and a single conflict used to discard the
    entire response.
    """
    for _ in range(MERGE_ROUNDS):
        merged = _merge_once(types, model=model)
        if len(merged) >= len(types):
            return merged
        types = merged
    return types


def _merge_once(types: list[EntityType], *, model: str | None) -> list[EntityType]:
    if len(types) < 3:
        return types
    system = load_prompt("entity_types.merge")
    listing = "\n".join(
        f"[{i}] {t.name}  ({t.n_referents} referents, {t.n_docs} pages)\n"
        f"     {t.definition}\n"
        f"     e.g. {', '.join(t.examples[:5])}"
        for i, t in enumerate(types, start=1)
    )
    data = _complete_json(system, f"Derived types to consolidate:\n\n{listing}", model=model)
    raw = (data or {}).get("types")
    if not isinstance(raw, list) or not raw:
        return types

    merged: list[EntityType] = []
    claimed: set[int] = set()
    for raw_entry in cast(list[Any], raw):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, Any], raw_entry)
        name = str(entry.get("type_name") or "").strip().lower().replace(" ", "_")
        indices = _member_indices(entry, len(types))
        # Partial acceptance: keep the first claim on an index and drop later duplicates,
        # rather than throwing away every good merge because one index was repeated.
        indices = [i for i in indices if i not in claimed]
        if not name or not indices:
            continue
        claimed.update(indices)
        members = [types[i] for i in indices]
        merged.append(
            EntityType(
                name=name,
                definition=str(entry.get("definition") or "").strip(),
                examples=[e for m in members for e in m.examples][:8],
                n_referents=sum(m.n_referents for m in members),
                n_docs=max(m.n_docs for m in members),
            )
        )
    if not merged:
        return types
    # Anything the model left unassigned survives as its own type — never silently dropped.
    merged.extend(t for i, t in enumerate(types) if i not in claimed)
    return sorted(merged, key=lambda t: -t.n_referents)


# --- deterministic stages ---------------------------------------------------------------
def fold(mentions: list[Mention]) -> list[Referent]:
    """Group surface variants into approximate referents by lexical key.

    Nothing is filtered on frequency any more, so this is no longer load-bearing for a
    threshold — it keeps a type's member and page counts honest (one referent, not one per
    spelling) and saves an embedding call per variant.

    Which is also why this stays a cheap rule rather than becoming LLM entity resolution.
    A rule cannot see an acronym as its expansion, or a nickname as its product, and doing
    better means blocking then adjudicating — real machinery, and non-deterministic.
    The payoff no longer justifies it: a missed fold now costs one inflated member count,
    because both spellings still land in the same group and get the same type. Resolution
    proper belongs where identity actually matters — keying facts, within a type, downstream.
    """
    surfaces: OrderedDict[str, Counter[str]] = OrderedDict()
    pages: dict[str, set[str]] = {}
    roles: dict[str, list[str]] = {}
    for m in mentions:
        key = normalize_surface(m.surface)
        surfaces.setdefault(key, Counter())[m.surface] += 1
        pages.setdefault(key, set()).add(m.page)
        if m.role:
            roles.setdefault(key, []).append(m.role)

    referents = [
        Referent(
            canonical=counts.most_common(1)[0][0],
            variants=list(counts),
            pages=pages[key],
            roles=roles.get(key, [])[:4],
        )
        for key, counts in surfaces.items()
    ]
    referents.sort(key=lambda r: -r.n_docs)
    return referents


# --- artifact ---------------------------------------------------------------------------


def load_taxonomy(taxonomy_id: int | None = None) -> dict[str, str]:
    """``{type name: definition}`` for the active taxonomy.

    ``taxonomy_id`` resolves a specific one instead — how a consumer reads back the types it
    keyed facts under, rather than assuming the active taxonomy still means the same thing.

    Falls back to ``DEFAULT_TYPES`` when nothing has been derived, mirroring how the
    relevance scorer degrades to cosine without its model file: a deployment that has never
    run a derivation still works, with types that are generic rather than tailored.
    """
    row = entity_taxonomy.get(taxonomy_id) if taxonomy_id is not None else entity_taxonomy.active()
    if row is None:
        return dict(DEFAULT_TYPES)

    defs: dict[str, str] = {}
    for raw_type in cast(list[Any], row.types or []):
        if not isinstance(raw_type, dict):
            continue
        entry = cast(dict[str, Any], raw_type)
        name, definition = entry.get("name"), entry.get("definition")
        if isinstance(name, str) and isinstance(definition, str) and name and definition:
            defs[name] = definition
    return defs or dict(DEFAULT_TYPES)


def derive(
    pages: list[tuple[str, str]],
    *,
    model: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the whole derivation over ``(path, body)`` pairs. Returns the artifact."""
    mentions: list[Mention] = []
    for n, (path, body) in enumerate(pages, start=1):
        mentions.extend(extract_page(path, body, model=model))
        if progress:
            progress("extract", n, len(pages))

    referents = fold(mentions)
    titles = {path.rsplit("/", 1)[-1].removesuffix(".md").lower() for path, _ in pages}
    artifacts: Counter[str] = Counter()
    surviving: list[Referent] = []
    for r in referents:
        reason = is_corpus_artifact(r.canonical, titles)
        if reason:
            artifacts[reason] += 1
        else:
            surviving.append(r)

    if not surviving:
        raise RuntimeError("no referents found; corpus may be too small")

    texts = [
        f"{r.canonical} -- {'; '.join(r.roles[:3])}" if r.roles else r.canonical for r in surviving
    ]
    vectors = embeddings.embed_texts(texts)
    if vectors is None:
        raise RuntimeError("embeddings unavailable; cannot group referents")
    unit = [_normalize(v) for v in vectors]
    order = sorted(range(len(surviving)), key=lambda i: -surviving[i].n_docs)
    groups = [[surviving[i] for i in g] for g in _leader_cluster(unit, order, GROUP_SIMILARITY)]
    groups.sort(key=len, reverse=True)

    # Every group is named. There is no cap: naming cost is proportional to the corpus, which
    # is known before the run starts, and a cap that silently drops groups would report full
    # coverage while typing only part of the wiki.
    log.info("entity_types: naming %d group(s)", len(groups))
    named: list[EntityType] = []
    for n, group in enumerate(groups, start=1):
        named.extend(name_group(group, model=model))
        if progress:
            progress("name", n, len(groups))

    # Types the LLM named identically across groups are one type.
    collapsed: OrderedDict[str, EntityType] = OrderedDict()
    for t in named:
        prior = collapsed.get(t.name)
        if prior:
            prior.examples = (prior.examples + t.examples)[:8]
            prior.n_referents += t.n_referents
            prior.n_docs = max(prior.n_docs, t.n_docs)
        else:
            collapsed[t.name] = t

    final = merge_types(list(collapsed.values()), model=model)

    # Hash the CONTENT, not just path and length: an edit that preserves length would
    # otherwise leave the fingerprint unchanged, and this is what tells you whether the
    # corpus has moved far enough to warrant re-deriving.
    fingerprint = embeddings.content_sha256("\n".join(f"{p}\n{b}" for p, b in sorted(pages)))
    return {
        "derived_at": datetime.now(timezone.utc).isoformat(),
        "corpus_fingerprint": fingerprint,
        "provenance": {
            "model": model or "(default)",
            "embedding_model": embeddings.model_name(),
            "group_similarity": GROUP_SIMILARITY,
        },
        "stats": {
            "n_pages": len(pages),
            "n_mentions": len(mentions),
            "n_referents": len(referents),
            "n_artifacts_dropped": sum(artifacts.values()),
            "artifacts_by_reason": dict(artifacts),
            "n_typed": len(surviving),
            "n_groups": len(groups),
            "n_types_named": len(collapsed),
            "n_types": len(final),
        },
        "entity_types": [t.model_dump() for t in final],
    }


def run_derivation(
    *, prefix: str = "", model: str | None = None, triggered_by_user_id: str | None = None
) -> dict[str, Any]:
    """Derive the taxonomy from the current wiki and store it. Returns the artifact.

    The callable entry point — a caller invokes this rather than shelling out. Raises
    RuntimeError when the corpus is too small to derive from or embeddings are unavailable;
    the caller decides whether that is fatal (nothing is stored, so the previous taxonomy,
    or the fallback, stays in force).
    """
    pages = read_corpus(prefix)
    if not pages:
        raise RuntimeError("no wiki pages to derive from")

    log.info("entity_types: deriving from %d page(s)", len(pages))
    artifact = derive(pages, model=model)

    stats = artifact["stats"]
    log.info(
        "entity_types: %d mention(s) -> %d referent(s) -> %d typed -> %d type(s)",
        stats["n_mentions"],
        stats["n_referents"],
        stats["n_typed"],
        stats["n_types"],
    )
    artifact["taxonomy_id"] = store_taxonomy(artifact, triggered_by=triggered_by_user_id)
    return artifact


def store_taxonomy(artifact: dict[str, Any], *, triggered_by: str | None = None) -> int:
    """Persist a derived taxonomy and make it active. Returns its id.

    Append-only: see ``app.db.entity_taxonomy`` for why a rename must not overwrite.
    """
    return entity_taxonomy.record(artifact, triggered_by=triggered_by)


def read_corpus(prefix: str = "") -> list[tuple[str, str]]:
    """``(path, body)`` for every tracked ``.md`` page. Reads the wiki, never a document."""
    out: list[tuple[str, str]] = []
    for path in wiki_git.list_paths(prefix):
        if not path.endswith(".md"):
            continue
        try:
            body = filesystem.absolute(path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if body.strip():
            out.append((path, body))
    return out
