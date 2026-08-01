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

    def xautoclaim(self, key, group, consumer, min_idle_time, start_id, count):
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
