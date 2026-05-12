"""HTTP request/response shapes for the launchers + agent_sessions routers.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# GET /api/launchers — catalog                                                #
# --------------------------------------------------------------------------- #


class LauncherCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    tagline: str
    icon_url: str
    kind: Literal["local_cli", "in_app", "web_handoff"]
    # AF#10 — frontend filters by this flag; in_app tools without a
    # backend launch path are gated out of the Run radio.
    available_for_launch: bool
    setup_status: dict[str, Any]
    # AF#14 — when caller passes ``machine_id`` + ``wiki_path``, this is
    # the resolved working-dir default for this page. Null otherwise.
    default_working_dir: str | None = None


class LauncherCatalog(BaseModel):
    launchers: list[LauncherCatalogEntry]


# --------------------------------------------------------------------------- #
# POST /api/launch                                                            #
# --------------------------------------------------------------------------- #


class LaunchRequest(BaseModel):
    tool_id: str
    wiki_path: str | None = None
    working_dir: str | None = None
    # R4#3 — cap message at 16KB so an attacker can't pad first_turn_prompt
    # via the user-controlled field.
    message: str = Field(..., max_length=16_384)
    resume_session_id: str | None = None
    machine_id: str | None = None  # AF#14
    remember_workdir_for_page: bool = False


class LaunchResponse(BaseModel):
    launch_code: str
    uri: str
    agent_session_id: str


# --------------------------------------------------------------------------- #
# POST /api/launch/exchange (helper-facing)                                   #
# --------------------------------------------------------------------------- #


class ExchangeRequest(BaseModel):
    code: str
    machine_id: str


class ExchangePayload(BaseModel):
    session_id: str
    working_dir: str | None
    first_turn_prompt: str | None  # absent on resume
    cli_session_id: str | None  # present only on resume


class ExchangeResponse(BaseModel):
    mcp_token: str
    endpoint: str
    manifest: dict[str, Any]
    payload: ExchangePayload


# --------------------------------------------------------------------------- #
# Probe endpoints                                                             #
# --------------------------------------------------------------------------- #


class ProbeAckRequest(BaseModel):
    nonce: str
    helper_port: int
    # AF#14 — frontend reads this back from probe-status so it can
    # default-fill working dirs from page_working_dirs.
    machine_id: str | None = None


class ProbeStatusResponse(BaseModel):
    acked: bool
    helper_port: int | None = None
    machine_id: str | None = None


# --------------------------------------------------------------------------- #
# Agent-session endpoints                                                     #
# --------------------------------------------------------------------------- #


class AgentSessionSummary(BaseModel):
    id: str
    tool_id: str
    wiki_path: str | None
    working_dir: str | None
    status: str
    started_at: str
    last_activity_at: str
    closed_at: str | None
    cli_session_id: str | None


class AgentSessionList(BaseModel):
    sessions: list[AgentSessionSummary]


class CliSessionUpdateRequest(BaseModel):
    cli_session_id: str


class CloseRequest(BaseModel):
    reason: str | None = None
