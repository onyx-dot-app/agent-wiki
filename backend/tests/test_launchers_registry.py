"""Manifest pydantic model + DSL validator."""

from __future__ import annotations

import pytest

from app.launchers import registry


def _valid_claude_manifest() -> dict:
    return {
        "manifest_version": 1,
        "id": "claude-code",
        "name": "Claude Code",
        "tagline": "Anthropic's terminal coding agent.",
        "icon_url": "/icons/claude-code.svg",
        "kind": "local_cli",
        "cli_check": {
            "binary": "claude",
            "version_flag": "--version",
            "min_version": "1.0.0",
            "install_hint_url": "https://example.com",
        },
        "mcp_config_format": "claude_json",
        "first_turn_prompt_delivery": {
            "method": "prompt_file_flag",
            "flag": "--prompt-file",
        },
        "launch": {
            "binary": "claude",
            "argv": ["--mcp-config", "${mcp_config_path}"],
            "env": {
                "AGENTWIKI_SESSION_ID": "${session_id}",
                "AGENTWIKI_ENDPOINT": "${endpoint}",
            },
            "cwd": "${working_dir}",
        },
        "resume": {
            "binary": "claude",
            "argv": [
                "--resume",
                "${cli_session_id}",
                "--mcp-config",
                "${mcp_config_path}",
            ],
            "env": {"AGENTWIKI_SESSION_ID": "${session_id}"},
            "cwd": "${working_dir}",
        },
        "session_id_capture": {
            "source": "file_watch",
            "path": "${home}/.claude/projects/${dirhash}/",
            "pattern": "*.jsonl",
            "extract": "filename_basename",
        },
    }


def test_valid_manifest_parses():
    m = registry.Manifest.model_validate(_valid_claude_manifest())
    assert m.id == "claude-code"
    assert m.kind == "local_cli"


def test_unknown_var_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("${not_a_var}")
    with pytest.raises(ValueError, match="unknown interpolation var"):
        registry.Manifest.model_validate(bad)


def test_token_in_argv_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("Bearer ${token}")
    with pytest.raises(ValueError, match="\\$\\{token\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_first_turn_prompt_anywhere_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("${first_turn_prompt}")
    with pytest.raises(ValueError, match="\\$\\{first_turn_prompt\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_first_turn_prompt_in_resume_rejected():
    bad = _valid_claude_manifest()
    bad["resume"]["argv"].append("${first_turn_prompt}")
    with pytest.raises(ValueError, match="\\$\\{first_turn_prompt\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_prompt_file_path_in_resume_rejected():
    """R2 audit #5 — resume.argv must not reference the prompt tmpfile."""
    bad = _valid_claude_manifest()
    bad["resume"]["argv"].append("${prompt_file_path}")
    with pytest.raises(ValueError, match="\\$\\{prompt_file_path\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_unknown_manifest_version_rejected():
    bad = _valid_claude_manifest()
    bad["manifest_version"] = 2
    with pytest.raises(ValueError):
        registry.Manifest.model_validate(bad)


def test_in_app_kind_skips_launch_block():
    m = registry.Manifest.model_validate(
        {
            "manifest_version": 1,
            "id": "onyx-craft",
            "name": "Onyx Craft",
            "tagline": "in-app",
            "icon_url": "/x.svg",
            "kind": "in_app",
            "task_kind": "craft_agent",
        }
    )
    assert m.kind == "in_app"
    assert m.task_kind == "craft_agent"
    assert m.launch is None


def test_local_cli_without_launch_rejected():
    bad = _valid_claude_manifest()
    bad.pop("launch")
    with pytest.raises(ValueError, match="local_cli manifest must have launch"):
        registry.Manifest.model_validate(bad)


def test_in_app_without_task_kind_rejected():
    with pytest.raises(ValueError, match="task_kind"):
        registry.Manifest.model_validate(
            {
                "manifest_version": 1,
                "id": "x",
                "name": "x",
                "tagline": "x",
                "icon_url": "/x.svg",
                "kind": "in_app",
            }
        )


def test_registry_loads_empty_when_dir_missing(tmp_path):
    r = registry.ManifestRegistry(tmp_path / "does_not_exist")
    assert r.list() == []


def test_registry_rejects_oversized_manifest(tmp_path):
    """R4#1 — DoS guard at registry load."""
    big = tmp_path / "huge.json"
    big.write_text("x" * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="exceeds"):
        registry.ManifestRegistry(tmp_path)


def test_registry_loads_valid_manifest_from_disk(tmp_path):
    import json

    (tmp_path / "claude.json").write_text(json.dumps(_valid_claude_manifest()))
    r = registry.ManifestRegistry(tmp_path)
    assert {m.id for m in r.list()} == {"claude-code"}
    assert r.get("claude-code") is not None
    assert r.get("nonexistent") is None
