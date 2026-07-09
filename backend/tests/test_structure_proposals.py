"""Structure-proposal repo (app/wiki/structure_proposals.py) — lifecycle and
concurrency guards. Real DB; the table lands via the migration chain.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.wiki import structure_proposals as proposals
from app.wiki.structure_proposals import ProposalEntryPoint, ProposalOp, ProposalStatus
from tests._seed import seed_user


def _mk(
    *,
    op: ProposalOp = ProposalOp.MERGE,
    source_paths: list[str] | None = None,
    target_paths: list[str] | None = None,
    base_shas: dict[str, str] | None = None,
    run_id: str = "run-1",
    instruction: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return proposals.create(
        op=op,
        source_paths=source_paths if source_paths is not None else ["a/dup.md", "b/dup.md"],
        target_paths=target_paths if target_paths is not None else ["a/dup.md"],
        base_shas=base_shas if base_shas is not None else {"a/dup.md": "sha-a", "b/dup.md": "sha-b"},
        summary="merge duplicate pages about the same topic",
        entry_point=ProposalEntryPoint.SWEEP,
        run_id=run_id,
        instruction=instruction,
        expires_at=expires_at,
    )


def test_create_and_get_roundtrip(tmp_db):
    row = _mk(instruction="fold b into a, keep both changelogs")
    got = proposals.get(row["id"])
    assert got is not None
    assert got["op"] == "merge"
    assert got["status"] == "pending"
    assert got["source_paths"] == ["a/dup.md", "b/dup.md"]
    assert got["base_shas"]["b/dup.md"] == "sha-b"
    assert got["instruction"] == "fold b into a, keep both changelogs"
    assert got["entry_point"] == "sweep"


def test_create_validates_paths(tmp_db):
    with pytest.raises(ValueError):
        _mk(source_paths=[])
    with pytest.raises(ValueError):
        _mk(op=ProposalOp.MOVE, target_paths=[])
    # delete_empty_folder legitimately has no target.
    row = _mk(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=["old-project"],
        target_paths=[],
        base_shas={},
    )
    assert row["op"] == "delete_empty_folder"


def test_approve_binds_acting_user(tmp_db):
    seed_user(uid="u_appr", email="appr@x.com")
    row = _mk()
    assert proposals.approve(row["id"], user_id="u_appr") is True
    got = proposals.get(row["id"])
    assert got is not None
    assert got["status"] == "approved"
    assert got["approved_by_user_id"] == "u_appr"
    assert got["acting_user_id"] == "u_appr"


def test_transitions_are_guarded(tmp_db):
    seed_user(uid="u_a", email="a@x.com")
    row = _mk()
    pid = row["id"]
    # Can't apply straight from pending.
    assert proposals.mark_applied(pid, applied_sha="sha-x") is False
    assert proposals.approve(pid, user_id="u_a") is True
    # Second approval loses the conditional-update race.
    assert proposals.approve(pid, user_id="u_a") is False
    # Reject only works from pending.
    assert proposals.reject(pid, user_id="u_a") is False
    assert proposals.mark_applied(pid, applied_sha="sha-x") is True
    got = proposals.get(pid)
    assert got is not None
    assert got["status"] == "applied"
    assert got["applied_sha"] == "sha-x"
    # Applied is terminal.
    assert proposals.mark_stale(pid, reason="drift") is False


def test_stale_from_pending_or_approved(tmp_db):
    seed_user(uid="u_a", email="a@x.com")
    p1 = _mk()
    assert proposals.mark_stale(p1["id"], reason="base sha moved") is True
    p2 = _mk()
    proposals.approve(p2["id"], user_id="u_a")
    assert proposals.mark_stale(p2["id"], reason="acl changed") is True
    got = proposals.get(p2["id"])
    assert got is not None
    assert got["status"] == "stale"
    assert got["status_reason"] == "acl changed"


def test_list_by_status_and_run(tmp_db):
    a = _mk(run_id="run-9")
    b = _mk(run_id="run-9", source_paths=["c/x.md"], target_paths=["d/x.md"], op=ProposalOp.MOVE, base_shas={"c/x.md": "s"})
    _mk(run_id="other")
    pending = proposals.list_by_status(ProposalStatus.PENDING)
    assert [p["id"] for p in pending][:2] == [a["id"], b["id"]]
    run = proposals.list_for_run("run-9")
    assert {p["id"] for p in run} == {a["id"], b["id"]}


def test_expire_pending_respects_ttl(tmp_db):
    fresh = _mk(expires_at="2999-01-01 00:00:00")
    due = _mk(expires_at="2020-01-01 00:00:00")
    no_ttl = _mk()
    n = proposals.expire_pending(older_than="2026-07-09 00:00:00")
    assert n == 1
    assert (proposals.get(due["id"]) or {})["status"] == "expired"
    assert (proposals.get(fresh["id"]) or {})["status"] == "pending"
    assert (proposals.get(no_ttl["id"]) or {})["status"] == "pending"


def test_op_check_constraint(tmp_db):
    from sqlalchemy.exc import IntegrityError

    from app.db.models import StructureProposal
    from app.db.session import session

    with pytest.raises(IntegrityError):
        with session() as s:
            s.add(
                StructureProposal(
                    op="explode",
                    source_paths=["a.md"],
                    summary="nope",
                    entry_point="sweep",
                )
            )


def test_create_folder_needs_no_source(tmp_db):
    row = _mk(
        op=ProposalOp.CREATE_FOLDER,
        source_paths=[],
        target_paths=["new-team-space"],
        base_shas={},
    )
    assert row["op"] == "create_folder"
    assert row["source_paths"] == []


def test_auto_approve_leaves_no_human_approver(tmp_db):
    from app.auth import users as users_repo

    row = _mk()
    assert (
        proposals.auto_approve(row["id"], acting_user_id=users_repo.AI_USER_ID)
        is True
    )
    got = proposals.get(row["id"])
    assert got is not None
    assert got["status"] == "approved"
    assert got["approved_by_user_id"] is None  # nobody clicked
    assert got["acting_user_id"] == users_repo.AI_USER_ID
    # Still races like any transition: a second auto-approve loses.
    assert proposals.auto_approve(row["id"], acting_user_id=users_repo.AI_USER_ID) is False
    # And proceeds to applied through the same gate as human-approved ones.
    assert proposals.mark_applied(row["id"], applied_sha="sha-auto") is True
