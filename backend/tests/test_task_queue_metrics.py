"""Queue observability: TaskQueue.oldest_age_seconds + the _TaskQueueCollector
that exposes per-queue depth/age gauges. Redis is faked so the age math and the
metric wiring are tested deterministically without a broker."""
from __future__ import annotations

from app import metrics
from app.tasks import queue as queue_mod


class _FakeRedis:
    """Stand-in for the calls depth() + oldest_age_seconds() make. Models the
    consumer-group boundary: entries with ms <= last_delivered_ms are treated as
    already delivered (in-flight), so an exclusive XRANGE skips them."""

    def __init__(self, *, entries_ms, now_ms, last_delivered_ms=None, as_bytes=False):
        self._entries = sorted(entries_ms)
        self._now_ms = now_ms
        self._last = last_delivered_ms
        self._as_bytes = as_bytes

    def _id(self, ms: int):
        s = f"{ms}-0"
        return s.encode() if self._as_bytes else s

    def xlen(self, key):
        return len(self._entries)

    def zcard(self, key):
        return 0

    def xpending(self, key, group):
        return {"pending": 0}

    def xinfo_groups(self, key):
        last = f"{self._last}-0" if self._last is not None else "0-0"
        if self._as_bytes:
            return [{b"name": b"workers", b"last-delivered-id": last.encode()}]
        return [{"name": "workers", "last-delivered-id": last}]

    def xrange(self, key, min="-", max="+", count=None):
        if min.startswith("("):  # exclusive lower bound "(<ms>-<seq>"
            boundary = int(min[1:].split("-")[0])
            ready = [ms for ms in self._entries if ms > boundary]
        else:
            ready = list(self._entries)
        return [(self._id(ready[0]), {})] if ready else []

    def time(self):
        return (self._now_ms // 1000, (self._now_ms % 1000) * 1000)


def test_oldest_age_seconds_from_stream_id(monkeypatch):
    # Stream id embeds enqueue time (ms); age = server-now - that, in seconds.
    fake = _FakeRedis(entries_ms=[10_000], now_ms=13_500)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() == 3.5


def test_oldest_age_seconds_none_when_empty(monkeypatch):
    fake = _FakeRedis(entries_ms=[], now_ms=13_500)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() is None


def test_oldest_age_seconds_excludes_in_flight(monkeypatch):
    # Oldest entry (5000) is already delivered (in-flight); age should reflect
    # the oldest *ready* entry (20000), not the in-flight one.
    fake = _FakeRedis(entries_ms=[5_000, 20_000], last_delivered_ms=5_000, now_ms=30_000)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() == 10.0

    # When the only entry is in-flight, nothing is ready → None.
    fake2 = _FakeRedis(entries_ms=[5_000], last_delivered_ms=5_000, now_ms=30_000)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake2)
    assert q.oldest_age_seconds() is None


def test_oldest_age_seconds_decodes_bytes_id(monkeypatch):
    # A non-decoding Redis client yields bytes ids; they must still parse to a
    # real age, not silently collapse to 0.0.
    fake = _FakeRedis(entries_ms=[10_000], now_ms=13_500, as_bytes=True)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)
    q = queue_mod.TaskQueue(name="coedit", max_size=1000)
    assert q.oldest_age_seconds() == 3.5


def test_task_queue_collector_labels_every_queue(monkeypatch):
    # pending = ready(xlen - in_flight) + delayed(zcard) = 2; age = (6000-1000)/1000.
    fake = _FakeRedis(entries_ms=[1_000, 2_000], now_ms=6_000)
    monkeypatch.setattr(queue_mod, "get_redis", lambda: fake)

    families = {f.name: f for f in metrics._TaskQueueCollector().collect()}
    assert set(families) == {"task_queue_depth", "task_queue_oldest_age_seconds"}

    depth = families["task_queue_depth"]
    seen = {s.labels["queue"] for s in depth.samples}
    assert seen == {
        "documents",
        "triggers",
        "coedit",
        "detection",
        "lightweight_maintenance",
    }
    assert all(s.value == 2 for s in depth.samples)

    age = families["task_queue_oldest_age_seconds"]
    assert {s.labels["queue"] for s in age.samples} == seen
    assert all(s.value == 5.0 for s in age.samples)
