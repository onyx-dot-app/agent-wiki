"""Queue observability: TaskQueue.oldest_age_seconds + the _TaskQueueCollector
that exposes per-queue depth/age gauges. Redis is faked so the age math and the
metric wiring are tested deterministically without a broker."""
from __future__ import annotations

from app import metrics
from app.tasks import queue as queue_mod


class _FakeRedis:
    """Minimal stand-in for the calls depth() + oldest_age_seconds() make."""

    def __init__(self, *, stream_len: int, oldest_ms: int | None, now_ms: int) -> None:
        self._len = stream_len
        self._oldest_ms = oldest_ms
        self._now_ms = now_ms

    def xlen(self, key):
        return self._len

    def zcard(self, key):
        return 0

    def xpending(self, key, group):
        return {"pending": 0}

    def xrange(self, key, count=None):
        if self._oldest_ms is None:
            return []
        return [(f"{self._oldest_ms}-0", {"payload": "x"})]

    def time(self):
        return (self._now_ms // 1000, (self._now_ms % 1000) * 1000)


def test_oldest_age_seconds_from_stream_id(monkeypatch):
    # Stream id embeds enqueue time (ms); age = server-now - that, in seconds.
    fake = _FakeRedis(stream_len=1, oldest_ms=10_000, now_ms=13_500)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() == 3.5


def test_oldest_age_seconds_none_when_empty(monkeypatch):
    fake = _FakeRedis(stream_len=0, oldest_ms=None, now_ms=13_500)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() is None


def test_task_queue_collector_labels_every_queue(monkeypatch):
    # pending = ready(xlen - in_flight) + delayed(zcard) = 2; age = (6000-1000)/1000.
    fake = _FakeRedis(stream_len=2, oldest_ms=1_000, now_ms=6_000)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)

    families = {f.name: f for f in metrics._TaskQueueCollector().collect()}
    assert set(families) == {"task_queue_depth", "task_queue_oldest_age_seconds"}

    depth = families["task_queue_depth"]
    seen = {s.labels["queue"] for s in depth.samples}
    assert seen == {"documents", "triggers", "coedit", "lightweight_maintenance"}
    assert all(s.value == 2 for s in depth.samples)

    age = families["task_queue_oldest_age_seconds"]
    assert {s.labels["queue"] for s in age.samples} == seen
    assert all(s.value == 5.0 for s in age.samples)
