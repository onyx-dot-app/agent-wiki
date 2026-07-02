"""Email dispatch in ``_record_fire``: only verified addresses receive mail,
the fire is recorded either way, and a send failure never loses the event."""
from __future__ import annotations

from typing import Any

import pytest

from app.email import service as email_service
from app.models.wiki import ChangeKind
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs
from app.triggers import email_verification
from app.triggers.engine import TriggerAction, TriggerRecord

from tests._seed import list_events, seed_user

_OWNER = "usr_1"


def _email_config(*, verified: bool) -> str:
    cfg = dest_configs.create(
        _OWNER, type="email", name="Me", config={"address": "nik@example.com"}
    )
    if verified:
        token = email_verification.mint_token(cfg["id"])
        assert email_verification.verify(token) == cfg["id"]
    return cfg["id"]


def _trigger(config_id: str) -> TriggerRecord:
    return TriggerRecord(
        id="trg_1",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        kind="delta",
        nl_description="fire when status changes",
        actions=[TriggerAction(destination_config_id=config_id, message="status changed")],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def _fire(trigger: TriggerRecord) -> None:
    _record_fire(
        trigger=trigger,
        action=trigger.actions[0],
        doc_path="projects/foo.md",
        sha="abc123",
        change_kind=ChangeKind.EDIT,
        reason="status flipped",
        instruction="say what changed",
        rendered_message="Status flipped to done",
        actor="U <u@x.com>",
    )


@pytest.fixture
def _sent(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_send(*, to: str, subject: str, text: str, html: str | None = None) -> None:
        calls.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr("app.tasks.triggers.email_service.send", fake_send)
    return calls


def test_verified_address_receives_message_with_doc_link(tmp_db, _sent):
    seed_user(uid=_OWNER)
    _fire(_trigger(_email_config(verified=True)))

    [call] = _sent
    assert call["to"] == "nik@example.com"
    assert "projects/foo.md" in call["subject"]
    assert "Status flipped to done" in call["text"]
    assert "/app/wiki/projects/foo.md" in call["text"]
    assert len(list_events(kind="trigger.fire")) == 1


def test_unverified_address_is_recorded_only(tmp_db, _sent):
    seed_user(uid=_OWNER)
    _fire(_trigger(_email_config(verified=False)))

    assert _sent == []
    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
    assert rows[0]["payload"]["destination_type"] == "email"


def test_send_failure_keeps_the_recorded_fire(tmp_db, monkeypatch):
    seed_user(uid=_OWNER)

    def boom(**kwargs):
        raise email_service.EmailSendError("could not connect")

    monkeypatch.setattr("app.tasks.triggers.email_service.send", boom)
    _fire(_trigger(_email_config(verified=True)))
    assert len(list_events(kind="trigger.fire")) == 1
