"""App-wide outbound email. One send seam for every consumer — trigger
destinations, notification emails, verification links — from the one
system address configured at /admin/email."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.email import settings as email_settings

log = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 15


class EmailSendError(RuntimeError):
    """A send failed. The message is sanitized — safe to surface to API
    clients; full transport detail goes to the server log."""


class EmailNotConfiguredError(EmailSendError):
    """Raised when sending is attempted before /admin/email is set up."""


def send(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one message through the configured SMTP account. Raises
    ``EmailSendError`` (or its ``EmailNotConfiguredError`` subclass) with a
    client-safe message — callers decide whether a failure is fatal."""
    cfg = email_settings.get()
    if not cfg.configured:
        raise EmailNotConfiguredError("SMTP is not configured (/admin/email)")

    msg = EmailMessage()
    msg["From"] = cfg.from_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        # Port 465 is implicit TLS; anything else negotiates STARTTLS.
        if cfg.port == 465:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=_SEND_TIMEOUT_SECONDS, context=context) as smtp:
                _login_and_send(smtp, cfg, msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=_SEND_TIMEOUT_SECONDS) as smtp:
                smtp.starttls(context=context)
                _login_and_send(smtp, cfg, msg)
    except smtplib.SMTPAuthenticationError as e:
        log.warning("email send to %s: SMTP auth rejected", to)
        raise EmailSendError(
            "SMTP authentication rejected (check the username and app password)"
        ) from e
    except smtplib.SMTPConnectError as e:
        log.warning("email send to %s failed to connect", to, exc_info=True)
        raise EmailSendError(
            "could not connect to the SMTP host (check host and port)"
        ) from e
    except smtplib.SMTPException as e:
        # Server-side logs keep the detail; clients get only the class.
        log.warning("email send to %s failed", to, exc_info=True)
        raise EmailSendError(f"{type(e).__name__} (see server logs)") from e
    except OSError as e:
        # smtplib exceptions subclass OSError, so bare socket/TLS failures
        # (refused, timeout, DNS) land here after the SMTP-specific branches.
        log.warning("email send to %s failed to connect", to, exc_info=True)
        raise EmailSendError(
            "could not connect to the SMTP host (check host and port)"
        ) from e
    log.info("email sent to=%s subject=%r", to, subject)


def _login_and_send(smtp: smtplib.SMTP, cfg: email_settings.EmailSmtpSettings, msg: EmailMessage) -> None:
    password = cfg.password.get_secret_value()
    if cfg.username and password:
        smtp.login(cfg.username, password)
    smtp.send_message(msg)
