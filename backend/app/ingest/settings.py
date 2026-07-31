"""DB-backed ingest settings. Configured from /admin/ingest."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import IngestSettings as IngestSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)

DEFAULT_MAX_DOC_CHARS = 100_000


DEFAULT_WARN_UPDATE_THRESHOLD = 30
DEFAULT_AUTO_UPDATE_CAP = 100


class IngestSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_doc_chars: int
    api_key: str | None
    # Outbound half of the "Onyx Connection" admin page — the public Onyx
    # origin for Craft launches. None = Craft unavailable.
    onyx_base_url: str | None
    # The organisation this wiki belongs to. Entity extraction is told not to treat it as a
    # referent: its name is on nearly every page, so it distinguishes nothing. A setting
    # rather than derived output — an admin knows it on day one, long before a corpus is big
    # enough for any statistical signal, and a re-derivation must not overwrite it.
    organization_name: str | None
    # "admin" | "inferred" | None. Gates inference: see the model for why the source, not the
    # value's nullness, decides whether a derivation may write.
    organization_name_source: str | None
    # Auto-update health knobs (see "Taming Bad-Behaved Wikis"): the default
    # per-page warning threshold owners can override, and a hard cap above which
    # a page's ingestion auto-update is turned off. 0 = off.
    warn_update_threshold_default: int
    auto_update_cap: int
    # Audit trail: when the row last changed and which admin changed it.
    updated_at: str | None
    updated_by_user_id: str | None


def get() -> IngestSettings:
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            return IngestSettings(
                max_doc_chars=DEFAULT_MAX_DOC_CHARS,
                api_key=None,
                onyx_base_url=None,
                organization_name=None,
                organization_name_source=None,
                warn_update_threshold_default=DEFAULT_WARN_UPDATE_THRESHOLD,
                auto_update_cap=DEFAULT_AUTO_UPDATE_CAP,
                updated_at=None,
                updated_by_user_id=None,
            )
        return IngestSettings(
            max_doc_chars=row.max_doc_chars,
            api_key=row.api_key,
            onyx_base_url=row.onyx_base_url,
            organization_name=row.organization_name,
            organization_name_source=row.organization_name_source,
            warn_update_threshold_default=row.warn_update_threshold_default,
            auto_update_cap=row.auto_update_cap,
            updated_at=row.updated_at,
            updated_by_user_id=row.updated_by_user_id,
        )


def get_onyx_base_url() -> str | None:
    """The admin-configured Onyx origin, or None when not set."""
    return get().onyx_base_url


def get_organization_name() -> str | None:
    """The organisation the wiki belongs to, or None when nobody has claimed it."""
    return get().organization_name


def organization_name_is_admin_set() -> bool:
    """Whether a human decided the name — in which case inference must not run.

    A derivation checks this before spending anything on detection: an admin's value is not
    to be improved upon, and that includes an admin who deliberately CLEARED it.
    """
    return get().organization_name_source == "admin"


def set_organization_name(
    name: str | None, *, source: str, updated_by_user_id: str | None = None
) -> None:
    """Claim the organisation name. ``source`` is "admin" or "inferred".

    Inference must not clobber a human decision, so an "inferred" write is refused once the
    source is "admin". An "admin" write always wins, including clearing the value.
    """
    if source not in ("admin", "inferred"):
        raise ValueError(f"unknown organization_name source: {source!r}")

    cleaned = (name or "").strip() or None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            row = IngestSettingsRow(id=1)
            s.add(row)
        if source == "inferred" and row.organization_name_source == "admin":
            log.info("organization_name: keeping the admin-set value; inference skipped")
            return
        row.organization_name = cleaned
        row.organization_name_source = source
        row.updated_at = now
        if updated_by_user_id is not None:
            row.updated_by_user_id = updated_by_user_id
    log.info("organization_name set (source=%s, cleared=%s)", source, cleaned is None)


def upsert(
    *,
    max_doc_chars: int,
    onyx_base_url: str | None,
    organization_name: str | None = None,
    warn_update_threshold_default: int | None = None,
    auto_update_cap: int | None = None,
    updated_by_user_id: str | None = None,
) -> None:
    """Upsert the connection fields, and the auto-update health knobs when
    provided. The two health knobs are ``None`` => leave unchanged (a new row
    falls back to the column defaults), so connection-only callers don't reset
    them."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            row = IngestSettingsRow(id=1)
            s.add(row)
        row.max_doc_chars = max_doc_chars
        row.onyx_base_url = onyx_base_url
        # Patch semantics, like the health knobs below: None leaves it unchanged, so a
        # connection-only save cannot silently clear an organisation name someone set.
        # A value arriving here came from an admin, so it is stamped as such — otherwise a
        # human edit would be indistinguishable from a guess and inference would overwrite it.
        if organization_name is not None:
            row.organization_name = organization_name.strip() or None
            row.organization_name_source = "admin"
        if warn_update_threshold_default is not None:
            row.warn_update_threshold_default = warn_update_threshold_default
        if auto_update_cap is not None:
            row.auto_update_cap = auto_update_cap
        row.updated_at = now
        row.updated_by_user_id = updated_by_user_id
    log.info(
        "ingest_settings upserted max_doc_chars=%d onyx_base_url_set=%s",
        max_doc_chars,
        bool(onyx_base_url),
    )


def regenerate_key(*, updated_by_user_id: str | None = None) -> str:
    """Generate a new API key, persist it, and return it."""
    key = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            s.add(
                IngestSettingsRow(
                    id=1,
                    max_doc_chars=DEFAULT_MAX_DOC_CHARS,
                    api_key=key,
                    updated_at=now,
                    updated_by_user_id=updated_by_user_id,
                )
            )
        else:
            row.api_key = key
            row.updated_at = now
            row.updated_by_user_id = updated_by_user_id
    log.info("ingest_settings: api_key regenerated by %s", updated_by_user_id)
    return key
