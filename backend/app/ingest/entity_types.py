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

import hashlib
import logging
import re
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, TypeVar, cast

from pydantic import BaseModel, Field

from app.config import CONFIG
from app.db import entity_type_taxonomy
from app.ingest.clustering import leader_cluster, normalize
from app.ingest import json_completion
from app.llm import embeddings
from app.llm.settings import get as get_llm_settings
from app.llm.prompts import load_prompt
from app.wiki import filesystem, git as wiki_git

log = logging.getLogger(__name__)

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")

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
# Runaway guard on the merge loop, not a working limit: the loop exits as soon as a round stops
# collapsing anything, so this only bounds a pathological case. Kept well clear of what real runs
# need — a 148-page corpus converged in 6 rounds, most of it in the first (221 -> 34), then a slow
# tail. One round is one LLM call, so headroom is nearly free, and running out is worse than
# paying for a round that finds nothing.
MERGE_ROUNDS = 20

# Output cap, well above the client's 4096 default. Extraction lists every referent on a page, so
# the response scales with the page — and on a 205k-char page it overflowed 4096, which cut the
# JSON off mid-list. Every one of the eight largest pages in a real 147-page wiki failed that way,
# contributing zero referents each. The cap costs nothing when unused, since billing follows
# tokens generated. It is a ceiling, not a guarantee: a page can still overflow it, which is why
# ``_complete_json`` now reports truncation instead of mistaking it for malformed JSON.
MAX_OUTPUT_TOKENS = 16384

# Concurrency for the two per-item LLM stages. Run sequentially, 147 pages took ~1.5 hours in
# production and a pod restart killed it before it could record anything — the calls are
# independent, so that wall clock was pure serialization. Eight matches the eval harness this
# module was ported from.
#
# Set by ``ENTITY_TYPE_DERIVE_WORKERS`` rather than fixed here, because the governing constraint
# is the PROVIDER's rate limit and no value is right for every deployment: each call can carry
# ~47k input tokens, and this pool runs INSIDE a queue task, so effective concurrency is the
# queue's own worker count multiplied by this. An operator on a lower tier has to be able to turn
# it down without rebuilding the image. Read per call, not captured at import, so a test (or a
# restart with a new value) takes effect.
DEFAULT_DERIVE_WORKERS = 8


def _derive_workers() -> int:
    """Concurrent extract/name calls. Never below 1 — 0 would mean a derivation that does
    nothing while reporting success."""
    return max(1, CONFIG.entity_type_derive_workers)

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
# --- LLM steps --------------------------------------------------------------------------
def _complete_json(
    system: str, user: str, *, model: str | None, ctx: str = ""
) -> dict[str, Any] | None:
    """One completion parsed as a JSON object — see ``app.ingest.json_completion``."""
    return json_completion.complete_json(
        system, user, model=model, ctx=ctx, module="entity_types", max_tokens=MAX_OUTPUT_TOKENS
    )


def extract_page(path: str, body: str, *, model: str | None = None) -> list[Mention]:
    """Open extraction over one whole page. No type menu — see module docstring."""
    system = load_prompt("entity_types.extract")
    data = _complete_json(
        system, f"path:\n{path}\ncontent:\n{body}", model=model, ctx=path
    )
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
    return json_completion.member_indices(entry, upper)


def _placeholder_name(members: list[Referent]) -> str:
    """A name unique to THIS member set.

    ``derive`` collapses types that share a name, on the premise that the model named them
    identically — which a placeholder does not carry. So the name must not be reachable by any
    other set of members, or two groups' referents and examples get pooled into one type.

    A digest over every member's canonical, rather than a readable slug alone: a slug has to be
    truncated somewhere, and two long names sharing a prefix would then collide. The leading slug
    is kept only so the name is legible to the merge step.
    """
    digest = hashlib.sha256(
        "\x1f".join(r.canonical for r in members).encode("utf-8")
    ).hexdigest()[:10]
    slug = _TOKENS.sub("_", members[0].canonical.lower()).strip("_")[:32]
    return f"unnamed_{slug}_{digest}" if slug else f"unnamed_{digest}"


def name_group(group: list[Referent], *, model: str | None = None) -> list[EntityType]:
    """Name the kind a group shares. May split a group that turns out to be mixed."""
    system = load_prompt("entity_types.name")
    listing = "\n".join(
        f"[{i}] {r.canonical}"
        + (f" -- {r.roles[0]}" if r.roles else "")
        + f"  (on {r.n_docs} page{'s' if r.n_docs != 1 else ''})"
        for i, r in enumerate(group, start=1)
    )
    data = _complete_json(
        system,
        f"Referents in this group:\n\n{listing}",
        model=model,
        ctx=f"naming a group of {len(group)} referent(s)",
    )

    # The prompt requires a partition: every member in exactly one type. Both ways it can break
    # take what is valid instead of discarding the response — losing one referent's type beats
    # losing the name of the whole group. Same rule as ``merge_types``.
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
        # A repeated member would inflate the support counts a type is judged on, so the FIRST
        # claim wins and later duplicates are dropped — not the entry, and not the response.
        fresh = [i for i in indices if i not in claimed]
        if len(fresh) != len(indices):
            log.warning(
                "entity_types: naming claimed %d already-assigned member(s) for %r; keeping the "
                "first assignment",
                len(indices) - len(fresh),
                name,
            )
        if not fresh:
            continue
        claimed.update(fresh)
        members = [group[i] for i in fresh]
        out.append(
            EntityType(
                name=name,
                definition=str(entry.get("definition") or "").strip(),
                examples=[r.canonical for r in members[:8]],
                n_referents=len(members),
                n_docs=len({p for r in members for p in r.pages}),
            )
        )

    # Unassigned members are carried as their own remainder rather than dropped silently: merge
    # places a handful from examples better than it places a whole group.
    uncovered = [i for i in range(len(group)) if i not in claimed]
    if out and uncovered:
        log.warning(
            "entity_types: naming covered %d of %d member(s); keeping the %d named type(s) and "
            "carrying %d uncovered member(s)",
            len(claimed),
            len(group),
            len(out),
            len(uncovered),
        )
        members = [group[i] for i in uncovered]
        out.append(
            EntityType(
                name=_placeholder_name(members),
                definition="(uncovered by naming)",
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
            name=_placeholder_name(group),
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
    trace = [len(types)]
    for round_n in range(1, MERGE_ROUNDS + 1):
        merged = _merge_once(types, model=model)
        if merged is None:
            log.warning(
                "entity_types: merge round %d failed; stopping at %d type(s), NOT converged: %s "
                "— the taxonomy may still be over-split",
                round_n,
                len(types),
                trace,
            )
            return types
        trace.append(len(merged))
        log.info(
            "entity_types: merge round %d: %d -> %d type(s)", round_n, len(types), len(merged)
        )
        if len(merged) >= len(types):
            log.info("entity_types: merge converged after %d round(s): %s", round_n, trace)
            return merged
        types = merged
    # Distinguished from convergence because it means the taxonomy is still collapsing and the
    # result is wherever the cap fell, not a stable answer.
    log.warning(
        "entity_types: merge hit the %d-round cap while still collapsing: %s — the taxonomy may "
        "still be over-split",
        MERGE_ROUNDS,
        trace,
    )
    return types


def _merge_once(types: list[EntityType], *, model: str | None) -> list[EntityType] | None:
    """One consolidation pass. ``None`` means the round FAILED — a provider error, a truncated
    response, or nothing usable parsed — as distinct from a round that ran and merged nothing.
    Returning the input for both would let a failure read as convergence, and the taxonomy would
    be persisted as a stable answer when it was only the point the failure happened."""
    if len(types) < 3:
        return types
    system = load_prompt("entity_types.merge")
    listing = "\n".join(
        f"[{i}] {t.name}  ({t.n_referents} referents, {t.n_docs} pages)\n"
        f"     {t.definition}\n"
        f"     e.g. {', '.join(t.examples)}"
        for i, t in enumerate(types, start=1)
    )
    data = _complete_json(
        system,
        f"Derived types to consolidate:\n\n{listing}",
        model=model,
        ctx=f"merging {len(types)} type(s)",
    )
    raw = (data or {}).get("types")
    if not isinstance(raw, list) or not raw:
        return None

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
        return None
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


def active_entity_type_taxonomy_id() -> int | None:
    """Id of the taxonomy in force, or None when nothing has been derived.

    A consumer that labels things with type names should read this ONCE per run and store it
    alongside what it wrote, so its labels stay resolvable through ``load_taxonomy(id)`` after
    a re-derivation renames a type. Reading it per item would let a derivation land mid-run and
    leave one batch labelled under two taxonomies.
    """
    row = entity_type_taxonomy.active()
    return row.id if row is not None else None


def load_taxonomy(entity_type_taxonomy_id: int | None = None) -> dict[str, str]:
    """``{type name: definition}`` for the active taxonomy.

    ``entity_type_taxonomy_id`` resolves a specific one instead — how a consumer reads back the types it
    keyed facts under, rather than assuming the active taxonomy still means the same thing.

    Falls back to ``DEFAULT_TYPES`` when nothing has been derived, mirroring how the
    relevance scorer degrades to cosine without its model file: a deployment that has never
    run a derivation still works, with types that are generic rather than tailored.
    """
    row = entity_type_taxonomy.get(entity_type_taxonomy_id) if entity_type_taxonomy_id is not None else entity_type_taxonomy.active()
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


def _guarded(fn: Callable[[_Item], list[_Result]], item: _Item, stage: str) -> list[_Result]:
    """Run one item, absorbing its failure. Preserves the module's rule that a single failed page
    or group must not abort a corpus-wide derivation — which a raised exception inside a pool
    would otherwise do, taking every other page's paid-for work with it."""
    try:
        return fn(item)
    except Exception:
        log.warning("entity_types: %s failed for one item", stage, exc_info=True)
        return []


def _map_ordered(
    fn: Callable[[_Item], list[_Result]],
    items: list[_Item],
    *,
    stage: str,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[list[_Result]]:
    """Map ``fn`` over ``items`` concurrently, returning results in INPUT order.

    The ordering is load-bearing, not cosmetic. ``_leader_cluster`` assigns each referent to the
    FIRST centroid it matches, and the naming collapse keeps first-seen examples — so a different
    arrival order can produce a different taxonomy from the same corpus. ``Executor.map`` yields
    by submission index rather than completion, which keeps a parallel run reproducible and
    identical to a sequential one.

    Progress therefore reports the index reached, not the number finished: with eight in flight,
    item 1 can still be running while 2-8 are done.
    """
    if not items:
        return []

    def one(item: _Item) -> list[_Result]:
        return _guarded(fn, item, stage)

    results: list[list[_Result]] = []
    workers = min(_derive_workers(), len(items))
    if workers <= 1:
        for n, item in enumerate(items, start=1):
            results.append(one(item))
            if progress:
                progress(stage, n, len(items))
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, result in enumerate(pool.map(one, items), start=1):
            results.append(result)
            if progress:
                progress(stage, n, len(items))
    return results


def derive(
    pages: list[tuple[str, str]],
    *,
    model: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the whole derivation over ``(path, body)`` pairs. Returns the artifact."""
    log.info(
        "entity_types: extracting from %d page(s), %d at a time", len(pages), _derive_workers()
    )
    mentions: list[Mention] = []
    for page_mentions in _map_ordered(
        lambda pb: extract_page(pb[0], pb[1], model=model),
        pages,
        stage="extract",
        progress=progress,
    ):
        mentions.extend(page_mentions)

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
    unit = [normalize(v) for v in vectors]
    order = sorted(range(len(surviving)), key=lambda i: -surviving[i].n_docs)
    groups = [[surviving[i] for i in g] for g in leader_cluster(unit, order, GROUP_SIMILARITY)]
    groups.sort(key=len, reverse=True)

    # Every group is named. There is no cap: naming cost is proportional to the corpus, which
    # is known before the run starts, and a cap that silently drops groups would report full
    # coverage while typing only part of the wiki.
    log.info("entity_types: naming %d group(s)", len(groups))
    named: list[EntityType] = []
    for group_types in _map_ordered(
        lambda g: name_group(g, model=model),
        groups,
        stage="name",
        progress=progress,
    ):
        named.extend(group_types)

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
    # Default to the admin's ingestion-pipeline model: this job is ingest-side and does not need
    # the main model. Unset means none was nominated, so fall through to the deployment default.
    model = model or get_llm_settings().ingest_selector_model or None

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
    artifact["entity_type_taxonomy_id"] = store_taxonomy(artifact, triggered_by=triggered_by_user_id)
    return artifact


def store_taxonomy(artifact: dict[str, Any], *, triggered_by: str | None = None) -> int:
    """Persist a derived taxonomy and make it active. Returns its id.

    Append-only: see ``app.db.entity_type_taxonomy`` for why a rename must not overwrite.
    """
    return entity_type_taxonomy.record(artifact, triggered_by=triggered_by)


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
