"""Notification emails go to the login address, gated per user by the
notify_* settings: comments fan out to the page owner and @mentioned users
(never the author), and update-frequency events mail the page owner."""
from __future__ import annotations

import pytest

from app.email.service import EmailSendError
from app.tasks.notify_emails import send_notification_email
from app.tasks.queues import triggers_queue
from app.tasks import update_frequency
from app.auth import users as users_repo
from app.wiki import acl
from app.wiki import comment_notifications

from tests._seed import seed_user


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, to, subject, text, html=None):
        calls.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr("app.tasks.notify_emails.send", fake_send)
    return calls


def _user(uid: str, email: str, **settings) -> str:
    seed_user(uid=uid, email=email)
    if settings:
        users_repo.update_settings(uid, settings)
    return uid


def test_send_respects_pref_and_kind(tmp_db, sent):
    _user("u_on", "on@x.com", notify_comment_email=True)
    _user("u_off", "off@x.com")

    with triggers_queue.immediate_mode():
        send_notification_email(
            user_id="u_on", kind="comment", subject="s", text="t"
        )
        send_notification_email(
            user_id="u_off", kind="comment", subject="s", text="t"
        )
        send_notification_email(
            user_id="u_on", kind="nonsense", subject="s", text="t"
        )

    assert [c["to"] for c in sent] == ["on@x.com"]


def test_send_failure_is_swallowed(tmp_db, monkeypatch):
    _user("u_on", "on@x.com", notify_comment_email=True)

    def boom(*, to, subject, text, html=None):
        raise EmailSendError("smtp down")

    monkeypatch.setattr("app.tasks.notify_emails.send", boom)
    with triggers_queue.immediate_mode():
        send_notification_email(
            user_id="u_on", kind="comment", subject="s", text="t"
        )  # must not raise


def test_comment_fans_out_to_owner_and_mentions_not_author(tmp_db, sent):
    author = _user("u_author", "author@x.com", notify_comment_email=True)
    _user("u_owner", "owner@x.com", notify_comment_email=True)
    _user("u_mention", "mention@x.com", notify_comment_email=True)
    acl.set_owner("notes/a.md", "u_owner")

    row = {
        "doc_path": "notes/a.md",
        "body": "ping @[Mention](mention:u_mention) and @[Author](mention:u_author)",
    }
    with triggers_queue.immediate_mode():
        comment_notifications.queue_for_comment(row, author_id=author)

    assert sorted(c["to"] for c in sent) == ["mention@x.com", "owner@x.com"]
    assert all("notes/a.md" in c["subject"] for c in sent)
    # mention tokens render as plain names in the mail body
    assert "mention:u_mention" not in sent[0]["text"]


def test_update_events_mail_the_owner(tmp_db, sent):
    _user("u_owner", "owner@x.com", notify_update_warning_email=True)
    acl.set_owner("notes/a.md", "u_owner")

    with triggers_queue.immediate_mode():
        update_frequency.record_auto_update_capped("notes/a.md", 12, 10)

    assert len(sent) == 1
    assert sent[0]["to"] == "owner@x.com"
    assert "paused" in sent[0]["subject"]


def test_update_event_without_owner_sends_nothing(tmp_db, sent):
    with triggers_queue.immediate_mode():
        update_frequency.record_auto_update_capped("notes/b.md", 12, 10)
    assert sent == []
