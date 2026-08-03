"""Entity-type derivation — a whole-space, unattended pass over the wiki.

Thin binding, like the other task modules: the work lives in
``app.ingest.entity_types``; this only says which queue it runs on.

``automanage_offline_queue`` is the right home by its own contract — batch,
nobody waiting, long and LLM-bound. A derivation makes one LLM call per page
plus one per candidate group, so it would starve connector ingest on
``documents``, and it breaks ``lightweight_maintenance``'s no-LLM rule.

Derivation is deliberately NOT scheduled. Two of its stages are LLM calls, so
re-running can rename a type — and anything keyed by the old name is orphaned.
It runs when someone asks for it, and the artifact records enough provenance
for that to be a deliberate migration rather than silent drift.
"""

from __future__ import annotations

import logging

from app.ingest import entity_types
from app.tasks.queues import automanage_offline_queue

log = logging.getLogger(__name__)


@automanage_offline_queue.task()
def derive_entity_types(
    triggered_by_user_id: str | None = None, model: str | None = None
) -> None:
    """Derive the taxonomy from the current wiki and store it.

    ``triggered_by_user_id`` is the admin who asked (NULL for a system run),
    matching ``run_detection_sweep``.

    ``model`` overrides the deployment default for this derivation only. Worth
    having because this job does not need the deployment's strongest model: the
    validated 9-type taxonomy for the reference wiki was produced by
    ``gpt-5.4-mini``, which is cheaper, faster, and materially less verbose —
    and verbosity is the risk here, since a stronger model came within 12% of
    the output cap on one page and truncation costs that page's referents
    entirely.

    Deliberately NOT wired to ``ingest_selector_model``: an empty value there
    means "skip the ingest pre-filter", so reusing it would let an admin turn
    off that filter and silently change which model builds the taxonomy.
    """
    entity_types.run_derivation(
        triggered_by_user_id=triggered_by_user_id, model=model
    )
