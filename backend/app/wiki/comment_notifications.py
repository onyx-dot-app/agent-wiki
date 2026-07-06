"""Fans a new comment or reply out to the people it concerns — the page
owner and any @mentioned users, never the author — as queued notification
emails. Recipients who haven't opted in are dropped by the send task."""
from __future__ import annotations

import logging
from typing import Any

from app.config import CONFIG
from app.tasks.notify_emails import send_notification_email
from app.wiki import acl
from app.wiki import comment_mentions

log = logging.getLogger(__name__)


def queue_for_comment(row: dict[str, Any], *, author_id: str) -> None:
    doc_path = str(row.get("doc_path") or "")
    body = str(row.get("body") or "")
    recipients = set(comment_mentions.mentioned_ids(body))
    owner = acl.get_owner(doc_path)
    if owner:
        recipients.add(owner)
    recipients.discard(author_id)
    if not recipients:
        return
    doc_link = f"{CONFIG.public_base_url}/app/wiki/{doc_path}"
    subject = f"Agent Wiki: new comment on {doc_path}"
    text = (
        f"{comment_mentions.detokenize(body)}\n\n"
        f"— comment on {doc_path}\n{doc_link}"
    )
    for user_id in sorted(recipients):
        send_notification_email(
            user_id=user_id, kind="comment", subject=subject, text=text
        )
