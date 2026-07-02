"""Slack connection storage: bot tokens round-trip encrypted per (user, team),
reads are owner-scoped, and connect states are single-use with a TTL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.db.models import SlackConnectState
from app.db.session import session
from app.slack import connect_state, connections

from tests._seed import seed_user

_TOKEN = "xoxb-agentwiki-test-token-0123456789"


def _connect(uid: str = "usr_1", team: str = "T123") -> None:
    connections.upsert(
        user_id=uid,
        team_id=team,
        team_name="Onyx Team",
        slack_user_id="U777",
        bot_token=_TOKEN,
        scope="chat:write,im:write",
    )


def test_upsert_get_and_masking(tmp_db):
    seed_user("usr_1")
    _connect()

    row = connections.get("usr_1", "T123")
    assert row is not None
    assert row["team_name"] == "Onyx Team"
    assert row["slack_user_id"] == "U777"
    assert "bot_token" not in row
    assert _TOKEN not in row["token_display"]
    assert row["token_display"].startswith(_TOKEN[:12])

    assert connections.get_bot_token("usr_1", "T123") == _TOKEN


def test_token_is_ciphertext_at_rest(tmp_db):
    seed_user("usr_1")
    _connect()
    # A LargeBinary-typed shadow of the table reads the stored bytes without
    # the EncryptedString decorator transparently decrypting them.
    raw_tbl = sa.table(
        "user_slack_connections",
        sa.column("user_id", sa.Text),
        sa.column("bot_token", sa.LargeBinary),
    )
    with session() as s:
        raw = s.execute(
            sa.select(raw_tbl.c.bot_token).where(raw_tbl.c.user_id == "usr_1")
        ).scalar_one()
    assert _TOKEN.encode() not in bytes(raw)


def test_upsert_replaces_existing(tmp_db):
    seed_user("usr_1")
    _connect()
    connections.upsert(
        user_id="usr_1",
        team_id="T123",
        team_name="Renamed",
        slack_user_id="U778",
        bot_token="xoxb-rotated-token-9876543210xyz",
        scope=None,
    )
    rows = connections.list_for_user("usr_1")
    assert len(rows) == 1
    assert rows[0]["team_name"] == "Renamed"
    assert connections.get_bot_token("usr_1", "T123") == "xoxb-rotated-token-9876543210xyz"


def test_get_is_owner_scoped(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="b@x.com")
    _connect()
    assert connections.get("usr_2", "T123") is None
    assert connections.get_bot_token("usr_2", "T123") is None


def test_delete_connection(tmp_db):
    seed_user("usr_1")
    _connect()
    assert connections.delete_connection("usr_1", "T123") is True
    assert connections.get("usr_1", "T123") is None
    assert connections.delete_connection("usr_1", "T123") is False


def test_state_mint_and_single_consume(tmp_db):
    seed_user("usr_1")
    state = connect_state.mint_state(user_id="usr_1", return_to="/settings")
    out = connect_state.consume_state(state, user_id="usr_1")
    assert out == {"return_to": "/settings"}
    # Replay is rejected.
    assert connect_state.consume_state(state, user_id="usr_1") is None


def test_state_rejects_foreign_user_and_garbage(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="b@x.com")
    state = connect_state.mint_state(user_id="usr_1", return_to=None)
    assert connect_state.consume_state(state, user_id="usr_2") is None
    assert connect_state.consume_state("not-a-state", user_id="usr_1") is None


def test_state_expires(tmp_db):
    seed_user("usr_1")
    state = connect_state.mint_state(user_id="usr_1", return_to=None)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(SlackConnectState, state)
        assert row is not None
        row.expires_at = past
    assert connect_state.consume_state(state, user_id="usr_1") is None


def test_mint_clears_prior_state(tmp_db):
    seed_user("usr_1")
    first = connect_state.mint_state(user_id="usr_1", return_to=None)
    connect_state.mint_state(user_id="usr_1", return_to=None)
    assert connect_state.consume_state(first, user_id="usr_1") is None


def _corrupt_stored_token(user_id: str) -> None:
    """Overwrite the ciphertext so any decrypt raises InvalidTag, simulating a
    rotated encryption key."""
    raw_tbl = sa.table(
        "user_slack_connections",
        sa.column("user_id", sa.Text),
        sa.column("bot_token", sa.LargeBinary),
    )
    with session() as s:
        s.execute(
            sa.update(raw_tbl)
            .where(raw_tbl.c.user_id == user_id)
            .values(bot_token=b"\x00" * 40)
        )


def test_undecryptable_token_reads_as_not_connected(tmp_db):
    seed_user("usr_1")
    _connect()
    _corrupt_stored_token("usr_1")

    # Status reads never touch the token column, so they still work.
    assert connections.get("usr_1", "T123") is not None
    assert len(connections.list_for_user("usr_1")) == 1

    # Resolving the token drops the stale row and reads as not-connected.
    assert connections.get_bot_token("usr_1", "T123") is None
    assert connections.get("usr_1", "T123") is None

    # Re-connecting after the drop works.
    _connect()
    assert connections.get_bot_token("usr_1", "T123") == _TOKEN


def test_upsert_and_delete_survive_undecryptable_token(tmp_db):
    seed_user("usr_1")
    _connect()
    _corrupt_stored_token("usr_1")

    # Overwriting never reads the old ciphertext.
    _connect()
    assert connections.get_bot_token("usr_1", "T123") == _TOKEN

    _corrupt_stored_token("usr_1")
    assert connections.delete_connection("usr_1", "T123") is True
    assert connections.list_for_user("usr_1") == []
