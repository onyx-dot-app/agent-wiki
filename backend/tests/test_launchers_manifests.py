"""All shipped manifests load + validate."""

from __future__ import annotations

from app.launchers.registry import get_registry, _reset_registry_for_tests


def setup_function(_):
    # Reset so each test re-reads from disk (some tests may patch
    # the manifest dir; the singleton would otherwise stick).
    _reset_registry_for_tests()


def test_all_shipped_manifests_load():
    r = get_registry()
    ids = {m.id for m in r.list()}
    assert ids == {"claude-code", "codex", "onyx-craft"}


def test_claude_manifest_token_argv_rule():
    """No ${token} in argv (AF/audit fix)."""
    r = get_registry()
    m = r.get("claude-code")
    assert m is not None
    assert m.launch is not None
    for a in m.launch.argv:
        assert "${token}" not in a
        assert "${first_turn_prompt}" not in a


def test_claude_manifest_no_token_in_env():
    """ — shipped manifest must NOT put token in env."""
    r = get_registry()
    m = r.get("claude-code")
    assert m is not None
    assert m.launch is not None
    for v in m.launch.env.values():
        assert "${token}" not in v


def test_codex_manifest_token_argv_rule():
    r = get_registry()
    m = r.get("codex")
    assert m is not None
    assert m.launch is not None
    for a in m.launch.argv:
        assert "${token}" not in a
        assert "${first_turn_prompt}" not in a


def test_codex_uses_file_watch_capture():
    """ — codex's session capture must be file_watch, never stdout_regex."""
    r = get_registry()
    m = r.get("codex")
    assert m is not None
    assert m.session_id_capture is not None
    assert m.session_id_capture.source == "file_watch"


def test_onyx_craft_is_in_app():
    r = get_registry()
    m = r.get("onyx-craft")
    assert m is not None
    assert m.kind == "in_app"
    assert m.task_kind == "craft_agent"
    assert m.launch is None
