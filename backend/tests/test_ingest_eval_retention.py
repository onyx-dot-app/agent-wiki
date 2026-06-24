"""Retention sweep for ingest_eval_samples — delete_older_than + the periodic task."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import CONFIG
from app.db.models import IngestEvalSample
from app.db.session import session
from app.ingest import eval_sample


def _seed(wiki_path: str, created_at: str) -> None:
    with session() as s:
        s.add(IngestEvalSample(
            source_content="c",
            wiki_path=wiki_path,
            wiki_body_before="b",
            outcome="irrelevant",
            created_at=created_at,
        ))


def _paths() -> set[str]:
    with session() as s:
        return {r.wiki_path for r in s.scalars(select(IngestEvalSample)).all()}


def test_delete_older_than_removes_old_keeps_recent(tmp_db):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _seed("old.md", "2020-01-01 00:00:00")
    _seed("new.md", now)
    deleted = eval_sample.delete_older_than(datetime(2021, 1, 1, tzinfo=timezone.utc))
    assert deleted == 1
    assert _paths() == {"new.md"}


def test_delete_older_than_respects_limit(tmp_db):
    for i in range(5):
        _seed(f"{i}.md", "2020-01-01 00:00:00")
    deleted = eval_sample.delete_older_than(datetime(2021, 1, 1, tzinfo=timezone.utc), limit=2)
    assert deleted == 2
    assert len(_paths()) == 3


def test_prune_task_deletes_beyond_retention(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "app.tasks.ingest_eval_retention.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_retention_days": 90}),
    )
    old = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    _seed("stale.md", old)
    _seed("fresh.md", recent)
    from app.tasks.ingest_eval_retention import prune_ingest_eval_samples
    from app.tasks.queues import lightweight_maintenance_queue
    with lightweight_maintenance_queue.immediate_mode():
        prune_ingest_eval_samples()
    assert _paths() == {"fresh.md"}


def test_prune_task_disabled_when_zero(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "app.tasks.ingest_eval_retention.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_retention_days": 0}),
    )
    _seed("ancient.md", "2020-01-01 00:00:00")
    from app.tasks.ingest_eval_retention import prune_ingest_eval_samples
    from app.tasks.queues import lightweight_maintenance_queue
    with lightweight_maintenance_queue.immediate_mode():
        prune_ingest_eval_samples()
    assert _paths() == {"ancient.md"}
