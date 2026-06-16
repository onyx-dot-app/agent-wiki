"""The `set_update_policy` chat/MCP tool (app/llm/agents/tools/set_update_policy.py).

Real wiki repo + real DB. `current_user` is patched to a seeded user so
`require_can("write")` resolves deterministically; unmanaged pages/folders are
implicit-public (read+write) except the explicit private-page case.
"""
from __future__ import annotations

from app.auth import User
from app.llm.agents.tools.set_update_policy import handle
from app.wiki import acl, git as wiki_git, update_policy
from tests._seed import seed_user

_PAGE = "guides/db.md"


def _commit(body: str = "# DB\n\nbody\n") -> None:
    wiki_git.commit_file(_PAGE, body, "seed", author=None)


def _as_user(monkeypatch, uid: str = "u_a") -> str:
    seed_user(uid=uid, email=f"{uid}@x.com")
    user = User(id=uid, email=f"{uid}@x.com", name=None, is_admin=False)
    monkeypatch.setattr("app.auth.current_user", lambda: user)
    monkeypatch.setattr(
        "app.llm.agents.tools.set_update_policy.current_user", lambda: user
    )
    return uid


def test_sets_disable_and_instruction(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    out = handle(
        {
            "path": _PAGE,
            "ingestion_auto_update_disabled": True,
            "update_instruction": "keep it terse",
        }
    )
    assert "error" not in out
    assert out["effective"]["ingestion_auto_update_disabled"] is True
    assert out["effective"]["update_instruction"] == "keep it terse"
    # Persisted, attributed to the acting user.
    assert update_policy.is_ingest_disabled(_PAGE) is True
    row = update_policy.get(_PAGE)
    assert row is not None and row["update_instruction"] == "keep it terse"
    assert row["updated_by_user_id"] == "u_a"


def test_patch_leaves_other_field(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    handle({"path": _PAGE, "ingestion_auto_update_disabled": True})
    out = handle({"path": _PAGE, "update_instruction": "be terse"})
    # Disable flag was set in the first call and must survive the second.
    assert out["effective"]["ingestion_auto_update_disabled"] is True
    assert out["effective"]["update_instruction"] == "be terse"


def test_empty_instruction_clears(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    handle({"path": _PAGE, "update_instruction": "x"})
    out = handle({"path": _PAGE, "update_instruction": ""})
    assert out["effective"]["update_instruction"] is None
    # No settings left → the row is removed.
    assert update_policy.get(_PAGE) is None


def test_folder_policy_without_file(tmp_repo, monkeypatch):
    _as_user(monkeypatch)
    out = handle({"path": "team", "ingestion_auto_update_disabled": True})
    assert "error" not in out
    assert out["kind"] == "folder"
    # Cascades to a (future) child page under the folder.
    assert update_policy.is_ingest_disabled("team/anything.md") is True


def test_page_not_found(tmp_repo, monkeypatch):
    _as_user(monkeypatch)
    out = handle({"path": "no/such.md", "ingestion_auto_update_disabled": True})
    assert "error" in out and "not found" in out["error"]


def test_requires_a_setting(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    out = handle({"path": _PAGE})
    assert "error" in out
    assert update_policy.get(_PAGE) is None


def test_denies_unauthorized_user(tmp_repo, monkeypatch):
    # Page private to Alice; Bob can't write its policy.
    wiki_git.commit_file(_PAGE, "# Spec\n", "seed")
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.on_page_created(_PAGE, owner_user_id=alice)
    for g in acl.list_for_path(_PAGE):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    _as_user(monkeypatch, uid="u_bob")
    out = handle({"path": _PAGE, "ingestion_auto_update_disabled": True})
    assert "error" in out
    assert update_policy.get(_PAGE) is None  # nothing written
