"""The card's freshness stamp and revival hygiene (backend).

Verbs are approve (do it) and reject (never again, content-scoped) — the
card's X is client-side only. Freshness: carried pendings get
`last_emitted_at` re-stamped each sweep, so the banner's "confirmed by the
last scan" line reflects the run that just re-verified the finding, not the
original emit. Revival hygiene: a revived finding is a fresh ask, so no
reviewer mark may linger on the row.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import git as wiki_git
from app.wiki.automanage import runner
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import (
    ProposalOp,
    ProposalStatus,
    get,
    list_by_status,
)
from tests._auth import login_fastapi
from tests._seed import seed_user


class _FixedDetector:
    pairs_paths = False

    def __init__(self, name: str, drafts: list[ProposalDraft]) -> None:
        self.name = name
        self.drafts = drafts

    def applicable(self, trigger: TriggerKind) -> bool:
        return trigger is TriggerKind.SWEEP

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        return list(self.drafts)

    def validate(self, proposal) -> str | None:
        return None


def _stub_draft(path: str) -> ProposalDraft:
    return ProposalDraft(
        op=ProposalOp.DELETE_PAGE,
        source_paths=[path],
        summary=f"remove {path}",
        auto_approvable=False,
    )


@pytest.fixture
def client(tmp_db, tmp_repo):
    wiki_git.commit_file("team/a.md", "# A\n", "seed", author=None)
    return TestClient(create_app())


def _sweep(monkeypatch, detectors) -> dict:
    monkeypatch.setattr(runner, "DETECTORS", detectors)
    return runner.run_sweep(triggered_by_user_id=None)


def _pending_one() -> dict:
    (row,) = list_by_status(ProposalStatus.PENDING)
    return row


def test_revival_clears_a_lingering_reviewer_mark(client, monkeypatch):
    """A revived finding is a fresh ask: no reviewer mark may linger on the
    row (the auto-apply visibility gate reads reviewed_by IS NULL as 'no
    human decision'). No verb produces a reviewed stale row today, so this
    guards the defensive clear in revive()."""
    from sqlalchemy import update as sa_update

    from app.db.models import ChangeProposal
    from app.db.session import session

    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    row = _pending_one()
    uid = seed_user(uid="u1", email="u@x.com")
    with session() as s:
        s.execute(
            sa_update(ChangeProposal)
            .where(ChangeProposal.id == row["id"])
            .values(status="stale", reviewed_by_user_id=uid)
        )

    _sweep(monkeypatch, [det])
    revived = get(row["id"])
    assert revived is not None and revived["status"] == "pending"
    assert revived["revive_count"] == 1
    assert revived["reviewed_by_user_id"] is None


def test_carried_pending_gets_freshness_restamped(client, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    row = _pending_one()
    first_stamp = row["last_emitted_at"]
    assert first_stamp is not None

    # Make the stamp visibly old, then re-sweep: the carry must re-stamp.
    from sqlalchemy import update as sa_update

    from app.db.models import ChangeProposal
    from app.db.session import session

    with session() as s:
        s.execute(
            sa_update(ChangeProposal)
            .where(ChangeProposal.id == row["id"])
            .values(last_emitted_at="2026-01-01 00:00:00")
        )

    _sweep(monkeypatch, [det])
    carried = get(row["id"])
    assert carried is not None
    assert carried["status"] == "pending"
    assert carried["last_emitted_at"] != "2026-01-01 00:00:00"  # re-stamped


def test_last_emitted_at_is_exposed_to_the_banner(client, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    uid = seed_user(uid="u1", email="u@x.com")
    login_fastapi(client, uid)

    resp = client.get("/api/automanage/proposals", params={"path": "team/a.md"})
    assert resp.status_code == 200
    (view,) = resp.json()["proposals"]
    assert view["last_emitted_at"] is not None
