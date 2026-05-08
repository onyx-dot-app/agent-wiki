"""Tests for the `run_bash` tool and its underlying ``_bash`` module.

Each test runs against ``tmp_repo`` so commands execute inside a real
tmp wiki working tree — no mocking of subprocess.
"""
from __future__ import annotations

import pytest

from app.llm.agents.tools import _bash
from app.llm.agents.tools.run_bash import handle


# --------------------------------------------------------------------------- #
# Chain parser                                                                #
# --------------------------------------------------------------------------- #


def test_parse_chain_simple():
    segs = _bash.parse_chain("ls -la")
    assert len(segs) == 1
    assert segs[0].command == "ls -la"
    assert segs[0].operator is None


def test_parse_chain_pipes_and_operators():
    segs = _bash.parse_chain("ls | grep foo && cat bar.md ; wc -l")
    ops = [s.operator for s in segs]
    assert ops == ["|", "&&", ";", None]


def test_parse_chain_respects_quotes():
    """Quoted operators inside `grep '|'` must NOT split the chain."""
    segs = _bash.parse_chain("grep '|' file.md")
    assert len(segs) == 1
    assert segs[0].command == "grep '|' file.md"


def test_parse_chain_respects_double_quotes_with_escapes():
    segs = _bash.parse_chain('grep "a && b" file.md | wc -l')
    assert len(segs) == 2
    assert segs[0].command == 'grep "a && b" file.md'
    assert segs[0].operator == "|"
    assert segs[1].command == "wc -l"


# --------------------------------------------------------------------------- #
# Allowlist                                                                   #
# --------------------------------------------------------------------------- #


def test_allowlist_blocks_rm(tmp_repo):
    out = handle({"command": "rm -rf ."})
    assert out["exit_code"] == 1
    assert "not allowed" in out["output"]
    assert "rm" in out["output"]


def test_allowlist_checks_every_segment_upfront(tmp_repo):
    """The whitelist gate runs against every parsed segment before any
    subprocess fires, so pipes can't smuggle in a disallowed command."""
    out = handle({"command": "ls | xargs rm"})
    assert out["exit_code"] == 1
    assert "not allowed" in out["output"]
    assert "xargs" in out["output"]


def test_allowlist_blocks_git(tmp_repo):
    out = handle({"command": "git log"})
    assert out["exit_code"] == 1
    assert "not allowed" in out["output"]


def test_empty_command_returns_error(tmp_repo):
    out = handle({"command": "   "})
    assert "error" in out
    assert out["error"] == "command is required"


def test_allowlist_blocks_unknown_command(tmp_repo):
    out = handle({"command": "curl https://example.com"})
    assert out["exit_code"] == 1
    assert "not allowed" in out["output"]


# --------------------------------------------------------------------------- #
# Execution semantics                                                         #
# --------------------------------------------------------------------------- #


def _seed_files(wiki_dir, files: dict[str, str]):
    from pathlib import Path

    root = Path(wiki_dir)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


def test_ls_runs_in_wiki_dir(tmp_repo, tmp_config):
    _seed_files(tmp_config.wiki_dir, {"alpha.md": "# A\n", "beta.md": "# B\n"})
    out = handle({"command": "ls"})
    assert out["exit_code"] == 0
    assert "alpha.md" in out["output"]
    assert "beta.md" in out["output"]


def test_pipe_passes_stdout_to_stdin(tmp_repo, tmp_config):
    _seed_files(tmp_config.wiki_dir, {"a.md": "x\n", "b.md": "y\n", "c.md": "z\n"})
    out = handle({"command": "ls | wc -l"})
    assert out["exit_code"] == 0
    # 3 .md files
    assert out["output"].strip() == "3"


def test_grep_no_match_is_not_an_error(tmp_repo, tmp_config):
    """`grep` returns rc=1 with empty stderr when nothing matches; the
    chain should still complete and return cleanly."""
    _seed_files(tmp_config.wiki_dir, {"a.md": "hello\n"})
    out = handle({"command": "grep 'no-such-string' a.md"})
    # rc=1 (no match), no [stderr] prefix because grep didn't write to stderr.
    assert out["exit_code"] == 1
    assert "[stderr]" not in out["output"]


def test_real_stderr_aborts_with_stderr_prefix(tmp_repo):
    out = handle({"command": "cat does-not-exist.md"})
    assert out["exit_code"] != 0
    assert out["output"].startswith("[stderr]")


def test_and_operator_short_circuits_on_failure(tmp_repo, tmp_config):
    """`grep no-match foo && ls` — the `ls` should not run."""
    _seed_files(tmp_config.wiki_dir, {"a.md": "hello\n"})
    out = handle({"command": "grep 'no-such' a.md && ls"})
    # `&&` stops on rc!=0; output should be empty (grep produced nothing).
    assert out["output"].strip() == ""
    assert out["exit_code"] == 1


def test_or_operator_short_circuits_on_success(tmp_repo, tmp_config):
    _seed_files(tmp_config.wiki_dir, {"a.md": "match\n"})
    out = handle({"command": "grep 'match' a.md || ls"})
    # lhs succeeded → `||` short-circuits; ls does NOT run.
    assert "match" in out["output"]
    assert "a.md" not in out["output"].split("\n")[0]


def test_semicolon_runs_independently(tmp_repo, tmp_config):
    _seed_files(tmp_config.wiki_dir, {"a.md": "hi\n"})
    out = handle({"command": "echo ignored ; ls"})
    # `echo` isn't allowlisted — should fail validation BEFORE running.
    # (Belt-and-suspenders for the allowlist.)
    assert out["exit_code"] == 1
    assert "not allowed" in out["output"]


# --------------------------------------------------------------------------- #
# Truncation                                                                  #
# --------------------------------------------------------------------------- #


def test_search_truncation_caps_grep_at_100_lines(tmp_repo, tmp_config):
    body = "\n".join(f"line{i} match" for i in range(200)) + "\n"
    _seed_files(tmp_config.wiki_dir, {"big.md": body})
    out = handle({"command": "grep match big.md"})
    assert out["truncated"] is True
    assert "more results truncated" in out["output"]
    assert out["output"].count("\n") < 200


def test_generic_truncation_caps_huge_output(tmp_repo, tmp_config):
    # 60 KB of text — past the 50 KB char cap.
    body = "x" * 60_000 + "\n"
    _seed_files(tmp_config.wiki_dir, {"big.md": body})
    out = handle({"command": "cat big.md"})
    assert out["truncated"] is True
    assert "output truncated" in out["output"]


def test_small_output_not_truncated(tmp_repo, tmp_config):
    _seed_files(tmp_config.wiki_dir, {"a.md": "hi\n"})
    out = handle({"command": "cat a.md"})
    assert out["truncated"] is False
    assert out["output"].rstrip() == "hi"


# --------------------------------------------------------------------------- #
# Misc                                                                        #
# --------------------------------------------------------------------------- #


def test_binary_output_refused(tmp_repo, tmp_config):
    from pathlib import Path

    p = Path(tmp_config.wiki_dir) / "blob.md"
    p.write_bytes(b"hello\x00\x00world\n")
    out = handle({"command": "cat blob.md"})
    assert out["exit_code"] == 1
    assert "binary" in out["output"]


def test_result_includes_elapsed_ms(tmp_repo):
    out = handle({"command": "ls"})
    assert "elapsed_ms" in out
    assert isinstance(out["elapsed_ms"], int)
    assert out["elapsed_ms"] >= 0


def test_registered_in_tool_registry():
    from app.llm.agents import tools

    assert "run_bash" in {s["name"] for s in tools.TOOL_SPECS}


def test_timeout_surfaces_user_friendly_error(tmp_repo, monkeypatch):
    """Drop the per-command timeout to 1s and run a sleeping command.

    `sleep` isn't allowlisted, so we monkey-patch ALLOWED_COMMANDS for
    this test only.
    """
    monkeypatch.setattr(_bash, "COMMAND_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        _bash,
        "ALLOWED_COMMANDS",
        frozenset(_bash.ALLOWED_COMMANDS | {"sleep"}),
    )
    out = handle({"command": "sleep 5"})
    assert out["exit_code"] == 1
    assert "timed out" in out["output"]
