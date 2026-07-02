"""DB-backed SMTP credentials for outbound email. Configured from /admin/email."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, SecretStr

from app.db.models import EmailSmtpSettings as EmailSmtpSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class EmailSmtpSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    port: int
    username: str
    # SecretStr so an incidental repr/str of the settings object redacts it.
    password: SecretStr
    from_address: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


_EMPTY = EmailSmtpSettings(host="", port=587, username="", password=SecretStr(""), from_address="")


def get() -> EmailSmtpSettings:
    with session() as s:
        row = s.get(EmailSmtpSettingsRow, 1)
        if row is None:
            return _EMPTY
        return EmailSmtpSettings(
            host=row.host,
            port=row.port,
            username=row.username,
            password=SecretStr(row.password),
            from_address=row.from_address,
        )


def upsert(*, host: str, port: int, username: str, password: str, from_address: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(EmailSmtpSettingsRow, 1)
        if row is None:
            s.add(
                EmailSmtpSettingsRow(
                    id=1, host=host, port=port, username=username,
                    password=password, from_address=from_address, updated_at=now,
                )
            )
        else:
            row.host = host
            row.port = port
            row.username = username
            row.password = password
            row.from_address = from_address
            row.updated_at = now
    log.info(
        "email_smtp_settings upserted host_set=%s from_set=%s password_set=%s",
        bool(host), bool(from_address), bool(password),
    )
