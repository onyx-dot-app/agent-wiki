"""Stale-entry reclaim (queue._reclaim_stale): adopting PEL entries whose
consumer died, and the poison-message delivery cap. Redis is faked so the
claim/ack/delete choreography is tested deterministically without a broker."""
from __future__ import annotations

import json
from typing import Any

from app.tasks import queue as queue_mod
from app.tasks.queue import TaskQueue, _reclaim_stale


class _FakeRedis:
    """Stand-in for the calls _reclaim_stale + _process_one make."""

    def __init__(self, claimed: list[tuple[str, dict[str, Any] | None]], delivered: dict[str, int]):
        self.claimed = claimed
        self.delivered = delivered  # entry id -> times_delivered after the claim
        self.acked: list[str] = []
        self.deleted: list[str] = []
        self.min_idle_seen: int | None = None

    def xautoclaim(self, key, group, consumer, min_idle_time, start_id, count):
        self.min_idle_seen = min_idle_time
        return ("0-0", self.claimed, [])

    def xpending_range(self, key, group, min, max, count):
        n = self.delivered.get(min)
        return [{"times_delivered": n}] if n is not None else []

    def xack(self, key, group, entry_id):
        self.acked.append(entry_id)
        return 1

    def xdel(self, key, entry_id):
        self.deleted.append(entry_id)
        return 1


def _payload(task: str) -> dict[str, str]:
    return {"payload": json.dumps({"task": task, "args": [], "kwargs": {}})}


def _queue_with_handler(calls: list[str]) -> TaskQueue:
    q = TaskQueue(name="reclaimq", max_size=10)
    q.handlers["record"] = lambda: calls.append("ran")
    return q


def test_reclaimed_entry_runs_and_completes():
    calls: list[str] = []
    q = _queue_with_handler(calls)
    r = _FakeRedis(claimed=[("1-0", _payload("record"))], delivered={"1-0": 2})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert calls == ["ran"]
    assert r.acked == ["1-0"] and r.deleted == ["1-0"]


def test_entry_over_delivery_cap_dropped_without_running():
    calls: list[str] = []
    q = _queue_with_handler(calls)
    over = queue_mod._MAX_DELIVERIES + 1
    r = _FakeRedis(claimed=[("1-0", _payload("record"))], delivered={"1-0": over})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert calls == []  # poison — never handed to the handler
    assert r.acked == ["1-0"] and r.deleted == ["1-0"]


def test_concurrently_deleted_entry_skipped():
    calls: list[str] = []
    q = _queue_with_handler(calls)
    r = _FakeRedis(claimed=[("1-0", None)], delivered={})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert calls == [] and r.acked == [] and r.deleted == []


def test_reclaim_survives_xautoclaim_failure():
    q = _queue_with_handler([])

    class _Boom(_FakeRedis):
        def xautoclaim(self, *a, **k):
            raise RuntimeError("redis down")

    _reclaim_stale(q, "worker-x", _Boom([], {}), vt_seconds=300, max_retries=3)  # no raise


def test_reclaim_horizon_is_vt_times_factor():
    # A merely-slow consumer must keep its entry until well past vt: reclaim
    # asks Redis for entries idle >= vt * _RECLAIM_IDLE_FACTOR, not vt itself.
    q = _queue_with_handler([])
    r = _FakeRedis(claimed=[], delivered={})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert r.min_idle_seen == 300 * queue_mod._RECLAIM_IDLE_FACTOR * 1000


def test_poison_drop_redis_failure_contained_and_next_entry_still_runs():
    # xack raising on the poison entry must neither escape _reclaim_stale nor
    # abort the rest of the claimed batch.
    calls: list[str] = []
    q = _queue_with_handler(calls)
    over = queue_mod._MAX_DELIVERIES + 1

    class _AckBoom(_FakeRedis):
        def xack(self, key, group, entry_id):
            if entry_id == "1-0":
                raise RuntimeError("redis down")
            return super().xack(key, group, entry_id)

    r = _AckBoom(
        claimed=[("1-0", _payload("record")), ("2-0", _payload("record"))],
        delivered={"1-0": over, "2-0": 2},
    )
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert calls == ["ran"]  # poison entry never ran; the good one did
    assert "2-0" in r.acked and "2-0" in r.deleted


def test_handler_exception_does_not_abort_batch():
    # A reclaimed entry whose handler raises is retried via _process_one's own
    # path; the failure must not stop later entries from being claimed and run.
    calls: list[str] = []
    q = _queue_with_handler(calls)

    def _boom():
        raise RuntimeError("handler failed")

    q.handlers["boom"] = _boom

    class _RetryCapture(_FakeRedis):
        def incr(self, key):
            return 1

        def hset(self, key, field, value):
            return 1

        def zadd(self, key, mapping):
            return 1

    r = _RetryCapture(
        claimed=[("1-0", _payload("boom")), ("2-0", _payload("record"))],
        delivered={"1-0": 2, "2-0": 2},
    )
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert calls == ["ran"]
    # the failing entry was acked+deleted by _process_one's retry path
    assert "1-0" in r.acked and "1-0" in r.deleted


class _OpOrderRedis(_FakeRedis):
    """Records the relative order of xdel/xack per entry."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ops: list[tuple[str, str]] = []

    def xack(self, key, group, entry_id):
        self.ops.append(("xack", entry_id))
        return super().xack(key, group, entry_id)

    def xdel(self, key, entry_id):
        self.ops.append(("xdel", entry_id))
        return super().xdel(key, entry_id)


def test_drop_deletes_from_stream_before_acking():
    # XDEL-then-XACK ordering: a failure between the calls must leave a
    # dangling PEL ref (cleaned by the next reclaim pass), never an acked
    # entry stranded in the stream where depth()/oldest_age count it forever.
    calls: list[str] = []
    q = _queue_with_handler(calls)
    r = _OpOrderRedis(claimed=[("1-0", _payload("record"))], delivered={"1-0": 2})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)
    assert r.ops == [("xdel", "1-0"), ("xack", "1-0")]


def test_poison_drop_ordering_and_partial_failure_leaves_no_zombie():
    # Even if XACK fails after XDEL succeeded, the entry is gone from the
    # stream — the dangling PEL ref is the recoverable half.
    q = _queue_with_handler([])
    over = queue_mod._MAX_DELIVERIES + 1

    class _AckAlwaysBoom(_OpOrderRedis):
        def xack(self, key, group, entry_id):
            self.ops.append(("xack", entry_id))
            raise RuntimeError("redis down")

    r = _AckAlwaysBoom(claimed=[("1-0", _payload("record"))], delivered={"1-0": over})
    _reclaim_stale(q, "worker-x", r, vt_seconds=300, max_retries=3)  # contained
    assert ("xdel", "1-0") in r.ops  # stream entry removed despite the ack failure
    assert r.deleted == ["1-0"]
