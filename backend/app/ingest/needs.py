"""Infer each wiki page's INFORMATION NEEDS — what it keeps track of, and how closely.

A need is not a fact on the page. It is a statement of what the page maintains: a stable
spec (``description``, ``detail_level``) plus a snapshot of the state it currently holds
(``current_content``). One page yields a handful, typically 1-5.

Why that framing pays: a page tracking "current deal status and blockers" has a need whose
*shape* is stable even as its content churns. An incoming document can then be judged against
the need — does it change what this page maintains? — rather than diffed against prose.

Each need also carries the ENTITIES it is about, typed from the derived taxonomy
(``app.ingest.entity_types``), and a ``focus`` saying whether the page's entity set is closed
or open. See ``InformationNeed`` for why both matter.

Naming, deliberately: a need is PRE-consolidation, so its label names the NEED — "training
data schema", "deal status" — never a topic. Seven needs naming facets of one model belong to
ONE topic, and no single page can know that; deriving the topic means comparing needs across
pages, and the naming step there is what turns a need into an aspect of a topic. So this step
names only what it can see, and the stored key says so rather than pretending otherwise.

Per page, and incremental: a need set is keyed by the page's stable doc id and guarded by a
content hash, so neither an unchanged page nor a renamed one is re-extracted. A re-run after one
edit costs one call — unlike the corpus-wide taxonomy, which is all-or-nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field

from app.db import page_needs
from app.ingest import entity_types
from app.llm import client
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)


class NeedKind(str, Enum):
    """How a page holds what it tracks.

    A closed set, and that is the point: upstream left this open-vocabulary, which let a model
    answer with an entity TYPE instead of an archetype. Downstream behaviour branches on it —
    a timeline appends an entry, an entity_status replaces a cell — so an unrecognized value is
    not a lossy label but a need nothing knows how to apply.
    """

    # The page logs things over time.
    TIMELINE = "timeline"
    # The page maintains the current state of something.
    ENTITY_STATUS = "entity_status"
    # Relatively static reference information.
    REFERENCE = "reference"
    OTHER = "other"


class Focus(str, Enum):
    """Whether a need's entity set is closed or open."""

    # This page is about these particular entities and only these — a single customer's account
    # page. A new entity is not admitted.
    SPECIFIC = "specific"
    # The page tracks a CLASS of thing and its entities are current instances — a deal tracker
    # with a row per customer. New instances should be added.
    GENERIC = "generic"


# An unusable focus reads as SPECIFIC — the fail-safe. Admitting an entity a page never asked
# for is worse than omitting one, so absence must not mean "open".
DEFAULT_FOCUS = Focus.SPECIFIC

MAX_VALIDATION_RETRIES = 1

# Output cap, well above the client's 4096 default. ``current_content`` enumerates a page's
# tracked entries rather than summarizing them, so the response scales with the page: measured
# over a 137-page production wiki, the worst page produced ~8.8k output tokens (~12.2k under a
# weaker model), against a p90 of ~2.4k. The default would truncate exactly the dense,
# many-entity pages this step exists to capture — and truncation lands as invalid JSON, so the
# page would retry, truncate again, and be recorded as tracking nothing. 16384 clears the
# measured worst case with headroom while staying inside current models' output limits; the cap
# costs nothing when unused, since billing follows tokens generated.
MAX_OUTPUT_TOKENS = 16384


class EntityMention(BaseModel):
    """A referent a need is about, typed against the taxonomy in force when extracted.

    A CANDIDATE, not a resolved entity: a surface name plus a type. Two pages naming the same
    company produce two mentions, and reconciling them is a later problem — an easier one once
    types exist, because it runs within a type rather than over all pairs.
    """

    canonical_name: str
    entity_type: str = ""
    # The subject the need is really about — the customer for a deal-status need. At most one
    # per need; the rest are associated referents. None primary is valid (a need about no
    # single subject), and it is what stops a status row being keyed by an aside.
    primary: bool = False


class InformationNeed(BaseModel):
    """One information need of one page."""

    # What THIS page tracks, as this page frames it. A need, not a topic — and not yet an
    # aspect either, which is what consolidation makes of it.
    need_name: str
    need_kind: NeedKind
    description: str
    detail_level: str = ""
    # The state the page holds right now — what an incoming document gets diffed against. A
    # description of what the page *tracks* is therefore a failure, not a shorter answer.
    current_content: str = ""
    entities: list[EntityMention] = Field(default_factory=list)
    # Whether the page's entity set is closed or open. Governs whether a document naming a NEW
    # entity may extend this need.
    focus: Focus = DEFAULT_FOCUS

    @property
    def primary_entity(self) -> EntityMention | None:
        return next((e for e in self.entities if e.primary), None)


class SchemaError(ValueError):
    """LLM JSON did not match the expected shape. The message is fed back to the model."""


def build_prompt(type_defs: dict[str, str]) -> str:
    """The extraction prompt, with the entity-type menu spliced in.

    The menu comes from the derived taxonomy rather than a hardcoded list, which is the whole
    point of deriving one: a deployment's own types instead of a guess at what every customer
    tracks.
    """
    menu = "\n".join(f"- {name}: {definition}" for name, definition in type_defs.items())
    block = (
        "Extract only referents matching one of these types (pick exactly one entity_type):\n"
        + menu
        + "\nPull the referent OUT of the need's phrasing (e.g. '<customer> POC deal' -> "
        "'<customer>'). If a need is a pure facet matching NONE of the types above (a 'status "
        "tracker', a 'timeline'), return no entities for it."
    )
    return load_prompt("needs.extract").replace("ENTITY_TYPES", block)


def _parse_entities(raw: object, type_defs: dict[str, str]) -> list[EntityMention]:
    """Structural validation, plus a check that the type came from the menu.

    An off-menu type is blanked rather than rejected: the mention is still evidence that the
    need is about something, and losing a whole need over a bad label costs more than the
    label is worth.
    """
    if not isinstance(raw, list):
        return []
    out: list[EntityMention] = []
    seen_primary = False
    for item in cast(list[Any], raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        name = str(entry.get("canonical_name") or entry.get("name") or "").strip()
        if not name:
            continue
        etype = str(entry.get("entity_type") or "").strip().lower()
        if etype and etype not in type_defs:
            log.debug("needs: dropping off-menu entity_type %r", etype)
            etype = ""
        # At most one primary. A second claim is demoted rather than honoured, so a status row
        # cannot end up keyed by whichever mention happened to be parsed last.
        primary = bool(entry.get("primary", False)) and not seen_primary
        seen_primary = seen_primary or primary
        out.append(EntityMention(canonical_name=name, entity_type=etype, primary=primary))
    return out


def parse_need(obj: object, ctx: str, type_defs: dict[str, str]) -> InformationNeed:
    """Validate one LLM-emitted need."""
    if not isinstance(obj, dict):
        raise SchemaError(f"{ctx} must be an object")
    entry = cast(dict[str, Any], obj)

    need_name = str(entry.get("need_name") or "").strip()
    if not need_name:
        raise SchemaError(f"{ctx}.need_name is required")
    description = str(entry.get("description") or "").strip()
    if not description:
        raise SchemaError(f"{ctx}.description is required")

    raw_kind = str(entry.get("need_kind") or "").strip().lower()
    try:
        need_kind = NeedKind(raw_kind)
    except ValueError:
        valid = ", ".join(k.value for k in NeedKind)
        raise SchemaError(f"{ctx}.need_kind is {raw_kind!r}; use one of: {valid}") from None

    # Unlike need_kind, an unusable focus is defaulted rather than rejected: it narrows what the
    # need may absorb, so getting it wrong costs a missed entity, not a misapplied fact.
    try:
        focus = Focus(str(entry.get("focus") or "").strip().lower())
    except ValueError:
        focus = DEFAULT_FOCUS

    return InformationNeed(
        need_name=need_name,
        need_kind=need_kind,
        description=description,
        detail_level=str(entry.get("detail_level") or "").strip(),
        current_content=str(entry.get("current_content") or "").strip(),
        entities=_parse_entities(entry.get("entities"), type_defs),
        focus=focus,
    )


def extract_page(
    path: str, body: str, *, type_defs: dict[str, str], model: str | None = None
) -> list[InformationNeed]:
    """Infer one page's needs. ONE call, the page sent whole.

    No chunking: a page seen only in slices cannot be partitioned into the things it tracks,
    which is precisely what this asks for. Returns [] on repeated failure — pages are
    independent, so a failure costs that page and nothing else.
    """
    system = build_prompt(type_defs)
    user = f"path:\n{path}\ncontent:\n{body}"
    last_error: str | None = None

    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        final = attempt == MAX_VALIDATION_RETRIES
        message = (
            user
            if last_error is None
            else f"{user}\n\nYOUR PREVIOUS OUTPUT WAS REJECTED: {last_error}\nReturn corrected JSON."
        )
        try:
            result = client.complete(
                [{"role": "system", "content": system}, {"role": "user", "content": message}],
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except Exception:  # noqa: BLE001 - one page must not abort a corpus-wide run
            log.warning("needs: completion failed for %s", path, exc_info=True)
            return []

        # Named explicitly because truncation is otherwise indistinguishable from a model that
        # simply emitted bad JSON, and the fix is different: raise the cap, don't re-prompt.
        # Providers pass their own vocabulary through, hence the substring match.
        if any(token in result.stop_reason.lower() for token in ("max_token", "length")):
            log.warning(
                "needs: response for %s hit the %d-token output cap — its needs will be "
                "incomplete or unparseable",
                path,
                MAX_OUTPUT_TOKENS,
            )

        text = (result.text or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            last_error = "response was not a JSON object"
            if final:
                break
            continue
        try:
            data = cast(dict[str, Any], json.loads(text[start : end + 1]))
        except json.JSONDecodeError:
            last_error = "response was not valid JSON"
            if final:
                break
            continue

        raw_needs = data.get("needs")
        if not isinstance(raw_needs, list):
            last_error = "top-level 'needs' must be a list"
            if final:
                break
            continue
        try:
            # Built whole and returned atomically: a mid-list failure must not leave half a
            # page's needs behind for the retry to append to again.
            return [
                parse_need(item, f"needs[{i}]", type_defs)
                for i, item in enumerate(cast(list[Any], raw_needs))
            ]
        except SchemaError as exc:
            last_error = str(exc)
            if final:
                break

    log.warning("needs: giving up on %s (%s)", path, last_error)
    return []


def run_extraction(
    *,
    prefix: str = "",
    model: str | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Extract needs for every page whose inputs have changed. Returns counts.

    Incremental: an unchanged page is skipped, so a re-run after one edit costs one call. A
    renamed page is also skipped — needs are keyed by the page's stable doc id, so a
    reorganization that changed no content costs nothing. ``force`` re-extracts everything,
    which is what a prompt change requires: stored needs are only comparable to each other when
    they came from the same prompt, and the prompt is not part of the guard.

    The entity-type menu is read ONCE per run and its taxonomy id stored with every need set,
    so a later re-derivation that renames a type leaves these mentions resolvable.
    """
    entity_type_taxonomy_id = entity_types.active_entity_type_taxonomy_id()
    type_defs = entity_types.load_taxonomy(entity_type_taxonomy_id)
    log.info(
        "needs: extracting with %d entity type(s) from taxonomy %s",
        len(type_defs),
        entity_type_taxonomy_id if entity_type_taxonomy_id is not None else "(fallback)",
    )

    pages = entity_types.read_corpus(prefix)
    by_path = dict(pages)
    stale = (
        [path for path, _ in pages]
        if force
        else page_needs.stale_paths(pages, model=model, entity_type_taxonomy_id=entity_type_taxonomy_id)
    )
    counts = {
        "pages": len(pages),
        "extracted": 0,
        "skipped": len(pages) - len(stale),
        "needs": 0,
        "empty": 0,
    }

    for n, path in enumerate(stale, start=1):
        needs = extract_page(path, by_path[path], type_defs=type_defs, model=model)
        if not needs:
            # "empty", not "failed": a page that tracks nothing durable and a page the model
            # could not parse both land here, and this layer cannot tell them apart —
            # extract_page logs the ones that were real failures. Either way the empty result
            # is STORED, which is what stops the page being re-extracted on every run.
            counts["empty"] += 1
        page_needs.store(
            path,
            body=by_path[path],
            needs=[need.model_dump(mode="json") for need in needs],
            model=model,
            entity_type_taxonomy_id=entity_type_taxonomy_id,
        )
        counts["extracted"] += 1
        counts["needs"] += len(needs)
        if progress:
            progress(n, len(stale))

    # Scoped to the prefix walked: ``by_path`` only describes that scope, so an unscoped prune
    # would read every page outside it as deleted.
    page_needs.prune(set(by_path), prefix=prefix)
    log.info(
        "needs: %d need(s) from %d page(s); %d unchanged, %d yielded nothing",
        counts["needs"],
        counts["extracted"],
        counts["skipped"],
        counts["empty"],
    )
    return counts
