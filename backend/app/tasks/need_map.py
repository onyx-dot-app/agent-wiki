"""Need-map derivation — a whole-corpus, unattended pass over the stored needs.

Thin binding, like the other task modules: the work lives in ``app.ingest.need_map``; this only
says which queue it runs on.

``automanage_offline_queue`` for the same reason as entity-type derivation: batch, nobody waiting,
long and LLM-bound. It makes one LLM call per cluster — around 150 on a 150-page wiki — so it
would starve connector ingest on ``documents``, and it breaks ``lightweight_maintenance``'s no-LLM
rule.

Deliberately NOT scheduled. Naming is an LLM call, so re-deriving can rename a topic or an aspect,
and ids are stable only within a map — anything holding a reference to the old one is orphaned. It
runs when someone asks, and the map records enough provenance for that to be a deliberate
re-derivation rather than silent drift.
"""

from __future__ import annotations

import logging

from app.ingest import need_map
from app.tasks.queues import automanage_offline_queue

log = logging.getLogger(__name__)


@automanage_offline_queue.task()
def derive_need_map(
    triggered_by_user_id: str | None = None, model: str | None = None
) -> None:
    """Cluster the stored needs, name the clusters, and record the map.

    ``triggered_by_user_id`` is the admin who asked (NULL for a system run), matching
    ``derive_entity_types``.

    ``model`` overrides the default for this run. Unset, ``run_consolidation`` uses the
    ingestion-pipeline model.
    """
    need_map.run_derivation(triggered_by=triggered_by_user_id, model=model)
