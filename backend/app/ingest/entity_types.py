"""Derive an entity-type taxonomy from the wiki corpus.

An entity type is a category of real-world thing the wiki tracks — an organization, a
person, a software product. Anything that keys facts by entity needs such a taxonomy, and
the obvious way to get one is for someone to write it down. That does not survive contact
with customers: nobody arriving with a wiki is going to author a type list first, and a list
written for one deployment is wrong for the next.

So derive it. Two things make that possible without asking anyone anything:

    home entities   a referent named on nearly every page carries no discriminative signal
                    (the corpus is *about* it). That is document frequency, not judgment.
    recurrence      "a real kind, not a one-off" is a document count.

The pipeline reads only the wiki — never an incoming document — because a taxonomy has to
describe the corpus it will be applied to:

    1. EXTRACT   per page, whole page, with NO type menu. Supplying one would presuppose the
                 answer; we want the referents the corpus actually contains.
    2. FOLD      merge spellings of one thing ("Jira"/"JIRA", "Scania"/"Scania AB").
                 LEXICAL, not semantic — see fold() for why embeddings fail here.
    3. FILTER    drop ambient referents and one-offs by document frequency.
    4. GROUP     cluster survivors by kind — this one IS semantic.
    5. NAME      one call per group: name the kind from its observed instances.
    6. MERGE     one call over the whole taxonomy; per-group naming cannot generalise.
    7. FLOOR     types with too little support fold into ``other``. Deterministic.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, cast

from app.llm import client, embeddings
from app.llm.prompts import load_prompt
from app.wiki import filesystem, git as wiki_git

log = logging.getLogger(__name__)

# --- thresholds -------------------------------------------------------------------------
# All distributional or count-based rather than fractions of the corpus. A fraction does not
# survive a change of scale: on a small wiki almost everything recurring clears 40% of pages,
# and on a large one nothing does, because a big corpus has whole areas that never mention
# the parent brand. Measured against the distribution instead, the home entity is a clear
# outlier and the same parameter works at either size.
AMBIENT_MULTIPLE = 3.0  # ambient when document frequency exceeds this * the 99th percentile
MIN_DOCS = 2  # a referent on one page is a one-off, not a recurring kind
GROUP_SIMILARITY = 0.35  # cosine floor for "same kind of thing"
MIN_TYPE_REFERENTS = 3  # a category needs this many distinct members to exist
MIN_TYPE_DOCS = 2  # ...spread over at least this many pages
OTHER_TYPE = "other"

MAX_NAMING_GROUPS = 200  # cost guard; groups beyond this fold into `other`
MERGE_ROUNDS = 3

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
# Folding is a LEXICAL job — the same name written differently — and embeddings are a
# semantic tool. Measured on a real corpus the bands overlap and no threshold separates them:
#     "Onyx"    vs "Onyx Cloud"   0.818   same thing, MISSED at 0.88
#     "Zendesk" vs "Freshdesk"    0.701   different things, only 0.12 lower
# So a similarity cut either misses variants or merges competitors.
#
# Containment is deliberately NOT used: "Onyx" is a prefix of "Onyx Cloud", but "Microsoft"
# is a prefix of "Microsoft Teams" — a vendor and its product. Real corpora contain that
# shape, so a containment rule would collapse vendors into their product lines.
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
    so "Scania AB" -> "scania" and "Onyx v4" -> "onyx", while "Microsoft Teams" stays
    "microsoft teams" — a vendor is not collapsed into its product.
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
@dataclass
class Mention:
    """One referent as a single page named it."""

    surface: str
    page: str
    role: str = ""


@dataclass
class Referent:
    """Folded surface variants — one approximate entity."""

    canonical: str
    variants: list[str] = field(default_factory=list)
    pages: set[str] = field(default_factory=set)
    roles: list[str] = field(default_factory=list)

    @property
    def n_docs(self) -> int:
        return len(self.pages)


@dataclass
class EntityType:
    """A derived type, carrying the evidence for its own existence."""

    name: str
    definition: str
    examples: list[str] = field(default_factory=list)
    n_referents: int = 0
    n_docs: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition,
            "examples": self.examples,
            "n_referents": self.n_referents,
            "n_docs": self.n_docs,
        }


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
    out: list[EntityType] = []
    for raw_entry in cast(list[Any], (data or {}).get("types") or []):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, Any], raw_entry)
        name = str(entry.get("type_name") or "").strip().lower().replace(" ", "_")
        if not name:
            continue
        indices = _member_indices(entry, len(group))
        members = [group[i] for i in indices] or group
        out.append(
            EntityType(
                name=name,
                definition=str(entry.get("definition") or "").strip(),
                examples=[r.canonical for r in members[:8]],
                n_referents=len(members),
                n_docs=len({p for r in members for p in r.pages}),
            )
        )
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

    Runs BEFORE any counting: variation splits an entity's document frequency across its
    spellings, and the ambient entity — the one we most need to detect — is the one most
    likely to be split below the threshold.
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


def split_by_frequency(referents: list[Referent]) -> dict[str, list[Referent]]:
    """Ambient / one-off / kept, by document frequency against the distribution."""
    dfs = sorted((r.n_docs for r in referents), reverse=True)
    # Index 1 at minimum: with few referents int(n * 0.01) is 0, which would make the
    # reference the maximum itself and the cut 3x the very referent being tested — so
    # nothing is ever ambient. The reference must never be the item under test.
    p99 = dfs[min(len(dfs) - 1, max(1, int(len(dfs) * 0.01)))] if len(dfs) > 1 else 1
    ambient_cut = max(2, int(p99 * AMBIENT_MULTIPLE))
    return {
        "ambient": [r for r in referents if r.n_docs >= ambient_cut],
        "oneoff": [r for r in referents if r.n_docs < MIN_DOCS],
        "kept": [r for r in referents if MIN_DOCS <= r.n_docs < ambient_cut],
    }


def apply_floor(types: list[EntityType]) -> list[EntityType]:
    """Fold under-supported types into ``other``.

    Not a cap on type COUNT — that would force merges regardless of evidence and would not
    scale. This is a floor in EVIDENCE units, so a larger corpus supports more types without
    changing the parameter. ``other`` is a first-class destination: for a referent with one
    sighting, "not enough evidence to type this yet" is the true answer, and forcing every
    referent into a named type is what pressures the namer into inventing a category for a
    single acronym.
    """
    keep: list[EntityType] = []
    folded: list[EntityType] = []
    for t in types:
        strong = t.n_referents >= MIN_TYPE_REFERENTS and t.n_docs >= MIN_TYPE_DOCS
        (keep if strong and t.name != OTHER_TYPE else folded).append(t)
    if folded:
        keep.append(
            EntityType(
                name=OTHER_TYPE,
                definition=(
                    "A named referent with too few sightings to establish its own category. "
                    "Not a kind of thing — a holding bucket, revisited as the corpus grows."
                ),
                examples=[e for t in folded for e in t.examples][:10],
                n_referents=sum(t.n_referents for t in folded),
                n_docs=max(t.n_docs for t in folded),
            )
        )
    return sorted(keep, key=lambda t: (t.name == OTHER_TYPE, -t.n_referents))


# --- artifact ---------------------------------------------------------------------------
def load_taxonomy(path: str | None) -> tuple[dict[str, str], frozenset[str]]:
    """``(type definitions, home entity names)`` from a derived artifact.

    Falls back to ``DEFAULT_TYPES`` when absent or unreadable, mirroring how the relevance
    scorer degrades to cosine when its model file is missing: a deployment that has never
    run the derivation still works.
    """
    if not path:
        return dict(DEFAULT_TYPES), frozenset()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = cast(dict[str, Any], json.load(handle))
    except (OSError, json.JSONDecodeError):
        log.warning("entity_types: no usable artifact at %s; using defaults", path)
        return dict(DEFAULT_TYPES), frozenset()
    defs: dict[str, str] = {}
    for raw_type in cast(list[Any], payload.get("entity_types") or []):
        if not isinstance(raw_type, dict):
            continue
        entry = cast(dict[str, Any], raw_type)
        name, definition = entry.get("name"), entry.get("definition")
        if isinstance(name, str) and isinstance(definition, str) and name and definition:
            defs[name] = definition
    stats = cast(dict[str, Any], payload.get("stats") or {})
    home = frozenset(str(a) for a in cast(list[Any], stats.get("ambient") or []))
    return (defs or dict(DEFAULT_TYPES)), home


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

    split = split_by_frequency(surviving)
    kept = split["kept"]
    if not kept:
        raise RuntimeError("no referents survived filtering; corpus may be too small")

    texts = [f"{r.canonical} -- {'; '.join(r.roles[:3])}" if r.roles else r.canonical for r in kept]
    vectors = embeddings.embed_texts(texts)
    if vectors is None:
        raise RuntimeError("embeddings unavailable; cannot group referents")
    unit = [_normalize(v) for v in vectors]
    order = sorted(range(len(kept)), key=lambda i: -kept[i].n_docs)
    groups = [[kept[i] for i in g] for g in _leader_cluster(unit, order, GROUP_SIMILARITY)]
    groups.sort(key=len, reverse=True)

    named: list[EntityType] = []
    for n, group in enumerate(groups[:MAX_NAMING_GROUPS], start=1):
        named.extend(name_group(group, model=model))
        if progress:
            progress("name", n, min(len(groups), MAX_NAMING_GROUPS))

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

    merged = merge_types(list(collapsed.values()), model=model)
    final = apply_floor(merged)

    fingerprint = embeddings.content_sha256("\n".join(sorted(f"{p}:{len(b)}" for p, b in pages)))
    return {
        "derived_at": datetime.now(timezone.utc).isoformat(),
        "corpus_fingerprint": fingerprint,
        "provenance": {
            "model": model or "(default)",
            "embedding_model": embeddings.model_name(),
            "ambient_multiple": AMBIENT_MULTIPLE,
            "min_docs": MIN_DOCS,
            "group_similarity": GROUP_SIMILARITY,
            "min_type_referents": MIN_TYPE_REFERENTS,
            "min_type_docs": MIN_TYPE_DOCS,
        },
        "stats": {
            "n_pages": len(pages),
            "n_mentions": len(mentions),
            "n_referents": len(referents),
            "n_artifacts_dropped": sum(artifacts.values()),
            "artifacts_by_reason": dict(artifacts),
            "n_ambient": len(split["ambient"]),
            "n_oneoff": len(split["oneoff"]),
            "n_kept": len(kept),
            "n_groups": len(groups),
            "n_types_named": len(collapsed),
            "n_types_merged": len(merged),
            "n_types": len(final),
            "ambient": [r.canonical for r in split["ambient"][:20]],
        },
        "entity_types": [t.to_json() for t in final],
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
    artifact["triggered_by_user_id"] = triggered_by_user_id

    stats = artifact["stats"]
    log.info(
        "entity_types: %d mention(s) -> %d referent(s) -> %d kept -> %d type(s); ambient=%s",
        stats["n_mentions"],
        stats["n_referents"],
        stats["n_kept"],
        stats["n_types"],
        ", ".join(stats["ambient"]) or "(none)",
    )
    store_taxonomy(artifact)
    return artifact


def store_taxonomy(artifact: dict[str, Any]) -> None:
    """Persist a derived taxonomy.

    A placeholder while there is no consumer in-tree. The eventual home is a versioned
    table rather than a single current value: types key facts by entity, so a re-derivation
    that renames one must not orphan rows keyed under the old name — which means keeping
    the old taxonomy resolvable, not overwriting it. Deferred until the consumer lands and
    can say what it needs to read.
    """
    log.info(
        "entity_types: derived taxonomy for corpus %s — not persisted; no store configured yet",
        str(artifact.get("corpus_fingerprint", ""))[:12],
    )


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
