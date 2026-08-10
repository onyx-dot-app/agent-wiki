"""Generate each aspect's CURRENT STATE — what its member needs say right now, unified.

The need map says which needs compose an aspect; the needs' own ``current_content`` snapshots
say what each page currently records. This step folds those snapshots into one answer per
aspect (``aspect_states``) — the comparison target reconciliation reads before touching any
page, and the place where two pages get caught disagreeing about the same facet.

Two paths, split by fan-out:

    single page   the aspect's state IS its needs' snapshots — copied mechanically, no LLM
    multi page    one completion unifies the per-page snapshots and flags disagreement

Only the second can set ``conflict``: unifying needs from more than one page is exactly where
incompatible claims surface, and that disagreement is a wiki inconsistency worth surfacing
(one page moved, its sibling did not), not a unification error. On the measured corpus the
split is ~234 mechanical to ~10 unified, so a full pass costs about ten calls.

Per aspect and resumable, like need extraction: each state is recorded as it is produced, and
an aspect is skipped when its stored state is already newer than every member's
``page_needs.updated_at`` — no stored fingerprint, deliberately; the timestamps already say
whether the inputs moved. A quiet corpus therefore costs a pass nothing.

States are scoped to their map: ``aspect_states`` CASCADEs from ``aspects``, and a derivation
mints new aspect rows, so a re-derivation starts stateless and a full pass re-pays the unified
calls. That is the price of map-scoped identity, accepted until the map itself becomes
patch-in-place.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from app.db import aspect_states, need_map, page_needs
from app.ingest import json_completion
from app.llm.prompts import load_prompt
from app.llm.settings import get as get_llm_settings

log = logging.getLogger(__name__)

# Same bound and rationale as the other corpus steps (see ``need_map.DEFAULT_WORKERS``).
DEFAULT_WORKERS = 8


class _Member(NamedTuple):
    """One member need, resolved from its (doc_id, need_name) link."""

    doc_id: str
    path: str
    need_name: str
    current_content: str
    needs_updated_at: str


class _Job(NamedTuple):
    """One aspect with its resolved members — everything a state needs."""

    aspect_id: int
    topic_name: str
    topic_description: str
    name: str
    description: str
    members: list[_Member]


def _resolve_members(
    links: list[need_map.NeedLink], by_doc: dict[str, page_needs.StoredNeeds]
) -> list[_Member]:
    """The member needs an aspect's links still resolve to.

    A link can dangle: the map is a snapshot, and a re-extraction since it was derived can have
    renamed or dropped the need it points at. Dangling links are skipped rather than failing the
    aspect — the state describes what the corpus still says, and the staleness of the map itself
    is the fingerprint's question (``need_maps.corpus_fingerprint``), not this step's.
    """
    members: list[_Member] = []
    for link in links:
        stored = by_doc.get(link.doc_id)
        if stored is None:
            continue
        for needdict in stored.needs:
            if str(needdict.get("need_name") or "") == link.need_name:
                members.append(
                    _Member(
                        doc_id=link.doc_id,
                        path=stored.path,
                        need_name=link.need_name,
                        current_content=str(needdict.get("current_content") or ""),
                        needs_updated_at=stored.updated_at,
                    )
                )
                break
    return members


def _mechanical_state(members: list[_Member]) -> str:
    """A single-page aspect's state: its needs' snapshots, verbatim.

    Usually one member; two needs of one page landing in one aspect is legitimate (see
    ``AspectPage``), and their snapshots are simply carried together.
    """
    return "\n\n".join(m.current_content for m in members if m.current_content.strip())


def _listing(members: list[_Member]) -> str:
    """The unification call's input: one block per member, attributed to its page."""
    blocks: list[str] = []
    for m in members:
        blocks.append(f"Page: {m.path}\nTracks: {m.need_name}\nCurrent snapshot: {m.current_content}")
    return "\n\n".join(blocks)


def _unify(job: _Job, *, model: str | None) -> tuple[str, bool, str] | None:
    """One completion: the aspect's unified state plus the disagreement verdict.

    None when the call fails — the aspect is skipped and counted, never guessed: a wrong
    "the pages agree" is worse than no state at all.
    """
    user = (
        f"Subject: {job.topic_name} — {job.topic_description}\n"
        f"Aspect: {job.name} — {job.description}\n\n"
        f"{_listing(job.members)}"
    )
    data = json_completion.complete_json(
        load_prompt("aspect_state.system"),
        user,
        model=model,
        ctx=f"aspect {job.name!r} across {len({m.doc_id for m in job.members})} page(s)",
        module="aspect_state",
    )
    if data is None:
        return None
    state = str(data.get("state") or "").strip()
    if not state:
        return None
    conflict = bool(data.get("conflict"))
    note = str(data.get("conflict_note") or "").strip() if conflict else ""
    return state, conflict, note


def _fresh(aspect_id: int, members: list[_Member], *, force: bool) -> bool:
    """Whether the stored state already reflects every member's current needs.

    Timestamp comparison, both sides written second-precision by the same clock discipline:
    a state generated after every member's last re-extraction has seen everything the members
    currently say.
    """
    if force:
        return False
    existing = aspect_states.get(aspect_id)
    if existing is None:
        return False
    return all(m.needs_updated_at <= existing.updated_at for m in members)


def run_generation(
    need_map_id: int | None = None,
    *,
    model: str | None = None,
    workers: int | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Generate states for one map's aspects (the active map when unnamed).

    Returns the pass's counts, or None when there is nothing to work on — no map, or a map
    with no aspects. Never partial-failure-fatal: one aspect's failed unification is counted
    and skipped, the same rule as every other corpus step.
    """
    record = need_map.get(need_map_id) if need_map_id is not None else need_map.active()
    if record is None or not record.topics:
        log.info("aspect_state: no need map to generate states for")
        return None

    llm = get_llm_settings()
    model = model or llm.ingest_selector_model or llm.model or None

    by_doc = {row.doc_id: row for row in page_needs.load_all()}
    jobs: list[_Job] = []
    dangling = 0
    for topic in record.topics:
        for aspect in topic.aspects:
            members = _resolve_members(aspect.needs, by_doc)
            if not members:
                dangling += 1
                continue
            jobs.append(
                _Job(
                    aspect_id=aspect.aspect_id,
                    topic_name=topic.name,
                    topic_description=topic.description,
                    name=aspect.name,
                    description=aspect.description,
                    members=members,
                )
            )
    if dangling:
        log.warning(
            "aspect_state: %d aspect(s) had no resolvable members and got no state "
            "(needs re-extracted since map %d was derived)",
            dangling,
            record.need_map_id,
        )

    todo = [j for j in jobs if not _fresh(j.aspect_id, j.members, force=force)]
    mech = [j for j in todo if len({m.doc_id for m in j.members}) == 1]
    fan = [j for j in todo if len({m.doc_id for m in j.members}) > 1]
    log.info(
        "aspect_state: map %d — %d aspect(s): %d fresh, %d mechanical, %d to unify",
        record.need_map_id,
        len(jobs),
        len(jobs) - len(todo),
        len(mech),
        len(fan),
    )

    for job in mech:
        aspect_states.record(
            job.aspect_id,
            state=_mechanical_state(job.members),
            conflict=False,
            conflict_note="",
            model="",
        )

    def one(job: _Job) -> str:
        result = _unify(job, model=model)
        if result is None:
            return "failed"
        state, conflict, note = result
        aspect_states.record(
            job.aspect_id, state=state, conflict=conflict, conflict_note=note, model=model or ""
        )
        return "conflict" if conflict else "ok"

    outcomes: list[str] = []
    if fan:
        with ThreadPoolExecutor(max_workers=workers or DEFAULT_WORKERS) as pool:
            outcomes = list(pool.map(one, fan))
    failed = outcomes.count("failed")
    conflicts = outcomes.count("conflict")

    stats = {
        "need_map_id": record.need_map_id,
        "aspects": len(jobs),
        "fresh": len(jobs) - len(todo),
        "mechanical": len(mech),
        "unified": len(fan) - failed,
        "failed": failed,
        "conflicts": conflicts,
        "dangling": dangling,
    }
    log.info("aspect_state: done — %s", stats)
    return stats
