"""Manifest pydantic model + DSL validator + on-disk loader.

A manifest is a JSON description of one coding tool the wiki can
launch. See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``
for the full spec.

This module defines pydantic models + enforces DSL rules:

- No ``${token}`` in ``launch.argv`` / ``resume.argv`` — the token
  reaches the helper via the ``AGENTWIKI_MCP_TOKEN`` env var, not the
  command line, so it doesn't surface in ``ps`` output.
- No ``${first_turn_prompt}`` anywhere — the helper materializes a
  tmpfile and the manifest references it via ``${prompt_file_path}``.
- No ``${prompt_file_path}`` in ``resume.*`` — the prompt tmpfile is
  first-turn only.
- No unknown ``${var}`` interpolation tokens.
- Manifest size cap at registry load.

The helper enforces the hardcoded **binary allow-list**; the backend
does NOT enforce it because the manifests are git-tracked and
reviewed at commit time. The validator here ensures the manifests are
well-formed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)


_ALLOWED_VARS = frozenset(
    {
        "token",
        "endpoint",
        "session_id",
        "cli_session_id",
        "working_dir",
        "first_turn_prompt",
        "prompt_file_path",
        "mcp_config_path",
        "home",
        "dirhash",
    }
)

_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")

_MAX_MANIFEST_BYTES = 64 * 1024  # DoS guard at registry load


def _find_vars(s: str) -> set[str]:
    return set(_VAR_RE.findall(s))


def _check_string(s: str, *, where: str) -> None:
    used = _find_vars(s)
    unknown = used - _ALLOWED_VARS
    if unknown:
        raise ValueError(f"unknown interpolation var(s) {sorted(unknown)} in {where}")


class CliCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    binary: str
    version_flag: str = "--version"
    min_version: str | None = None
    install_hint_url: str | None = None


class FirstTurnPromptDelivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["prompt_file_flag", "stdin", "none"]
    flag: str | None = None


class LaunchBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    binary: str
    argv: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    @model_validator(mode="after")
    def _validate_vars(self) -> "LaunchBlock":
        for i, a in enumerate(self.argv):
            _check_string(a, where=f"argv[{i}]")
            if "${token}" in a:
                raise ValueError(
                    "${token} forbidden in argv (token must come via env "
                    f"AGENTWIKI_MCP_TOKEN). Offending argv[{i}]={a!r}."
                )
            if "${first_turn_prompt}" in a:
                raise ValueError(
                    "${first_turn_prompt} forbidden in argv — helper "
                    "materializes a tmpfile; reference ${prompt_file_path} "
                    f"instead. Offending argv[{i}]={a!r}."
                )
        for k, v in self.env.items():
            _check_string(v, where=f"env.{k}")
            if "${first_turn_prompt}" in v:
                raise ValueError(
                    "${first_turn_prompt} forbidden in env (reference "
                    f"${{prompt_file_path}} instead). env.{k}={v!r}."
                )
        if self.cwd is not None:
            _check_string(self.cwd, where="cwd")
            if "${first_turn_prompt}" in self.cwd:
                raise ValueError("${first_turn_prompt} forbidden in cwd")
        return self


class ResumeBlock(LaunchBlock):
    """Resume blocks reject both ``${first_turn_prompt}`` AND
    ``${prompt_file_path}`` anywhere — the prompt tmpfile is only
    materialized for first-turn launches ."""

    @model_validator(mode="after")
    def _validate_resume_specific(self) -> "ResumeBlock":
        for i, a in enumerate(self.argv):
            if "${prompt_file_path}" in a:
                raise ValueError(
                    "${prompt_file_path} forbidden in resume.argv — "
                    "first-turn-only signal. "
                    f"Offending argv[{i}]={a!r}."
                )
        for k, v in self.env.items():
            if "${prompt_file_path}" in v:
                raise ValueError(f"${{prompt_file_path}} forbidden in resume.env.{k}")
        if self.cwd and "${prompt_file_path}" in self.cwd:
            raise ValueError("${prompt_file_path} forbidden in resume.cwd")
        return self


class SessionIdCapture(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["file_watch", "stdout_regex", "none"]
    path: str | None = None
    pattern: str | None = None
    extract: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_version: Literal[1]
    id: str
    name: str
    tagline: str
    icon_url: str
    kind: Literal["local_cli", "in_app", "web_handoff"]

    cli_check: CliCheck | None = None
    mcp_config_format: Literal["claude_json", "codex_toml", "none"] | None = None
    first_turn_prompt_delivery: FirstTurnPromptDelivery | None = None
    launch: LaunchBlock | None = None
    resume: ResumeBlock | None = None
    session_id_capture: SessionIdCapture | None = None

    # in_app-only
    task_kind: str | None = None
    stream_resource_uri: str | None = None

    @model_validator(mode="after")
    def _validate_kind_shape(self) -> "Manifest":
        if self.kind == "local_cli":
            if self.launch is None:
                raise ValueError("local_cli manifest must have launch block")
            if self.cli_check is None:
                raise ValueError("local_cli manifest must have cli_check")
            if self.first_turn_prompt_delivery is None:
                raise ValueError("local_cli manifest must specify first_turn_prompt_delivery")
        elif self.kind == "in_app":
            if self.task_kind is None:
                raise ValueError("in_app manifest must specify task_kind")
        return self


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


class ManifestRegistry:
    def __init__(self, manifest_dir: Path):
        self._by_id: dict[str, Manifest] = {}
        if not manifest_dir.exists():
            log.info("manifest dir missing: %s — registry empty", manifest_dir)
            return
        for p in sorted(manifest_dir.glob("*.json")):
            if p.stat().st_size > _MAX_MANIFEST_BYTES:
                raise ValueError(f"manifest {p} exceeds {_MAX_MANIFEST_BYTES}B cap")
            try:
                m = Manifest.model_validate_json(p.read_text())
            except Exception:
                log.exception("manifest %s failed validation; refusing to load", p)
                raise
            if m.id in self._by_id:
                raise ValueError(f"duplicate manifest id {m.id!r}")
            self._by_id[m.id] = m
            log.info("manifest loaded id=%s kind=%s", m.id, m.kind)

    def list(self) -> list[Manifest]:
        return list(self._by_id.values())

    def get(self, manifest_id: str) -> Manifest | None:
        return self._by_id.get(manifest_id)


_MANIFEST_DIR = Path(__file__).parent / "manifests"
_registry_singleton: ManifestRegistry | None = None


def get_registry() -> ManifestRegistry:
    """Lazy singleton — defers manifest-dir read until first use so the
    test/validator module can import the types without the dir needing
    to exist yet."""
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = ManifestRegistry(_MANIFEST_DIR)
    return _registry_singleton


def reset_registry_for_tests() -> None:
    """Test-only: drop the cached singleton so a fresh load picks up
    on-disk changes."""
    global _registry_singleton
    _registry_singleton = None


# Backwards-compatible alias (tests + future modules may reference either).
_reset_registry_for_tests = reset_registry_for_tests
