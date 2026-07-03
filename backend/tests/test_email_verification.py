"""Email destination verification: configs require an address, tokens are
single-use and TTL-bounded, and consuming a valid token stamps verified_at."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.db.models import EmailVerificationToken
from app.db.session import session
from app.triggers import destination_configs as dest_configs
from app.triggers import email_verification

from tests._seed import seed_user


def _get(cfg_id: str) -> dict:
    row = dest_configs.get(cfg_id, "usr_1")
    assert row is not None
    return row


def _email_config(owner: str = "usr_1", address: str = "nik@example.com") -> str:
    return dest_configs.create(
        owner, type="email", name="Me", config={"address": address}
    )["id"]


def test_create_returns_unverified(tmp_db):
    seed_user("usr_1")
    row = dest_configs.create(
        "usr_1", type="email", name="Me", config={"address": "nik@example.com"}
    )
    assert row["verified_at"] is None


def test_create_same_address_is_idempotent(tmp_db):
    seed_user("usr_1")
    first = dest_configs.create(
        "usr_1", type="email", name="Me", config={"address": "nik@example.com"}
    )
    again = dest_configs.create(
        "usr_1", type="email", name="Dup", config={"address": "NIK@example.com"}
    )
    assert again["id"] == first["id"]
    assert len(dest_configs.list_for_user("usr_1")) == 1


def test_email_config_requires_address(tmp_db):
    seed_user("usr_1")
    with pytest.raises(ValueError, match="address"):
        dest_configs.create("usr_1", type="email", name="No address")
    with pytest.raises(ValueError, match="address"):
        dest_configs.create("usr_1", type="email", name="Bad", config={"address": "not-an-email"})


def _age_token(token: str, *, seconds: int) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        s.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.token == token)
            .values(created_at=past)
        )


def test_verify_stamps_config_and_is_single_use(tmp_db):
    seed_user("usr_1")
    cfg_id = _email_config()
    assert _get(cfg_id)["verified_at"] is None

    token = email_verification.mint_token(cfg_id)
    assert email_verification.verify(token) == cfg_id
    assert _get(cfg_id)["verified_at"] is not None

    # Replay is rejected.
    assert email_verification.verify(token) is None


def test_verify_rejects_garbage(tmp_db):
    seed_user("usr_1")
    _email_config()
    assert email_verification.verify("not-a-token") is None
    assert email_verification.verify("") is None


def test_verify_rejects_expired_token(tmp_db):
    seed_user("usr_1")
    cfg_id = _email_config()
    token = email_verification.mint_token(cfg_id)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        s.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.token == token)
            .values(expires_at=past)
        )
    assert email_verification.verify(token) is None
    assert _get(cfg_id)["verified_at"] is None


def test_mint_is_rate_limited_then_remint_invalidates_prior(tmp_db):
    seed_user("usr_1")
    cfg_id = _email_config()
    first = email_verification.mint_token(cfg_id)
    with pytest.raises(email_verification.MintRateLimitedError):
        email_verification.mint_token(cfg_id)
    _age_token(first, seconds=120)
    email_verification.mint_token(cfg_id)
    assert email_verification.verify(first) is None
