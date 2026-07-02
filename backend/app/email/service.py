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


class EmailNotConfiguredError(RuntimeError):
    """Raised when sending is attempted before /admin/email is set up."""


def send(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one message through the configured SMTP account. Raises
    ``EmailNotConfiguredError`` when unconfigured and ``smtplib`` errors on
    transport failure — callers decide whether a failure is fatal."""
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

    context = ssl.create_default_context()
    # Port 465 is implicit TLS; anything else negotiates STARTTLS.
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=_SEND_TIMEOUT_SECONDS, context=context) as smtp:
            _login_and_send(smtp, cfg, msg)
    else:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=_SEND_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=context)
            _login_and_send(smtp, cfg, msg)
    log.info("email sent to=%s subject=%r", to, subject)


def _login_and_send(smtp: smtplib.SMTP, cfg: email_settings.EmailSmtpSettings, msg: EmailMessage) -> None:
    password = cfg.password.get_secret_value()
    if cfg.username and password:
        smtp.login(cfg.username, password)
    smtp.send_message(msg)
