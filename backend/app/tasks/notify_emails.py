"""Per-user notification emails: a queued fan-out that mails a copy of an
in-app activity signal to the user's login address (``users.email``). The
opt-in check lives here, next to the send, so producers stay fire-and-forget
and a preference flipped after enqueue is still honored."""
from __future__ import annotations

import logging

from app.auth import users as users_repo
from app.email.service import EmailNotConfiguredError, EmailSendError, send
from app.models.user_settings import UserSettings
from app.tasks.queues import triggers_queue

log = logging.getLogger(__name__)

# kind -> the UserSettings flag that gates it
_PREF_BY_KIND = {
    "comment": "notify_comment_email",
    "update_warning": "notify_update_warning_email",
}


@triggers_queue.task()
def send_notification_email(
    *, user_id: str, kind: str, subject: str, text: str
) -> None:
    pref = _PREF_BY_KIND.get(kind)
    if pref is None:
        log.warning("unknown notification email kind %r; dropping", kind)
        return
    user = users_repo.get_by_id(user_id)
    if user is None or not user.get("email"):
        return
    settings = UserSettings.model_validate(user.get("settings") or {})
    if not getattr(settings, pref):
        return
    try:
        send(to=user["email"], subject=subject, text=text)
    except EmailNotConfiguredError:
        log.info("notification email skipped (SMTP not configured)")
    except EmailSendError:
        log.exception("notification email to user %s failed", user_id)
