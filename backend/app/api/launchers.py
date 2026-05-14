"""HTTP API for coding-tool launchers.

Routes mounted under ``/api`` from ``app.main:create_app``:

- ``GET  /api/launchers``                 — catalog
- ``POST /api/launch``                    — mint launch code + session
- ``POST /api/launch/exchange``           — helper-facing (bearer = launch_code)
- ``POST /api/launch/probe-ack``          — helper acks URI probe
- ``GET  /api/launch/probe-status``       — frontend polls

All gated by ``CONFIG.launchers_enabled``.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``
+ ``implementation_plans/phase_1_backend.md`` (incl. AF / R-audit fix sections).
"""

from __future__ import annotations

from threading import RLock
from time import time

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import User
from app.auth import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.auth.deps import require_user
from app.config import CONFIG
from app.launchers import (
    launcher_tokens,
    page_dirs,
    prompt_builder,
    sessions as sessions_repo,
)
from app.launchers.registry import Manifest, get_registry
from app.models.launchers import (
    ExchangePayload,
    ExchangeRequest,
    ExchangeResponse,
    LaunchRequest,
    LaunchResponse,
    LauncherCatalog,
    LauncherCatalogEntry,
    ProbeAckRequest,
    ProbeStatusResponse,
)
from app.wiki import acl as wiki_acl
from app.wiki import filesystem as wiki_fs
from app.wiki import git as wiki_git
from app.wiki import linked_repos as wiki_linked_repos

router = APIRouter()


def _check_flag() -> None:
    if not CONFIG.launchers_enabled:
        raise HTTPException(status_code=404, detail="launchers disabled")


def _entry_available(m: Manifest) -> bool:
    """AF#10 — frontend filters by this flag. in_app tools without a
    backend launch path are not yet wired."""
    return m.kind == "local_cli"


# --------------------------------------------------------------------------- #
# GET /api/launchers — catalog                                                #
# --------------------------------------------------------------------------- #


@router.get("/launchers", response_model=LauncherCatalog)
def get_catalog(
    machine_id: str | None = None,
    wiki_path: str | None = None,
    user: User = Depends(require_user),
) -> LauncherCatalog:
    """List shipped launchers + per-tool setup status.

    When both ``machine_id`` and ``wiki_path`` are supplied, the response
    includes ``default_working_dir`` from ``page_working_dirs`` (AF#14).
    """
    _check_flag()
    has_token = len(tokens_repo.list_for_user(user.id)) > 0

    default_workdir: str | None = None
    if machine_id and wiki_path:
        default_workdir = page_dirs.get_for_page(
            user_id=user.id,
            machine_id=machine_id,
            wiki_path=wiki_path,
        )

    entries: list[LauncherCatalogEntry] = []
    for m in get_registry().list():
        entries.append(
            LauncherCatalogEntry(
                id=m.id,
                name=m.name,
                tagline=m.tagline,
                icon_url=m.icon_url,
                kind=m.kind,
                available_for_launch=_entry_available(m),
                setup_status={"token": has_token},
                default_working_dir=default_workdir,
            )
        )
    return LauncherCatalog(launchers=entries)


# --------------------------------------------------------------------------- #
# POST /api/launch — mint code + session                                      #
# --------------------------------------------------------------------------- #


def _maybe_read_page_body(wiki_path: str, user: User) -> tuple[str | None, list[str]]:
    """Read page body + parse linked_repos. ACL-gated (AF#1) and
    traversal-protected (R2#2). Returns ``(body, repos)`` or
    ``(None, [])`` if the file doesn't exist (acceptable — the wizard
    can launch on paths that exist only in the frontend).
    """
    try:
        canonical = wiki_fs.safe_rel_path(wiki_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid wiki_path") from exc

    if not wiki_acl.can(user.id, bool(user.is_admin), "read", canonical):
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        body = wiki_git.read_file(canonical)
    except Exception:
        # File doesn't exist or git ref missing — treat as no body.
        return None, []
    return body, wiki_linked_repos.parse_linked_repos(body)


@router.post("/launch", response_model=LaunchResponse)
def post_launch(
    req: LaunchRequest, request: Request, user: User = Depends(require_user)
) -> LaunchResponse:
    _check_flag()

    manifest = get_registry().get(req.tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"unknown tool_id {req.tool_id!r}")
    if manifest.kind != "local_cli":
        # in_app routes through /api/craft/launch (P2 #16 — not yet shipped).
        raise HTTPException(
            status_code=400,
            detail=f"tool {req.tool_id!r} kind={manifest.kind!r} not supported here",
        )

    # Resume branch.
    if req.resume_session_id is not None:
        existing = sessions_repo.get(req.resume_session_id)
        if existing is None or existing["user_id"] != user.id:
            raise HTTPException(status_code=404, detail="resume session not found")
        # R3#1 — refuse resume if a helper already holds the session.
        if existing["status"] in ("pending", "active"):
            raise HTTPException(
                status_code=409,
                detail="session already in flight; close it first",
            )
        first_turn_prompt = ""  # not replayed on resume — exchange omits it
        wiki_path = existing["wiki_path"]
        working_dir = existing["working_dir"]
        machine_id = existing["machine_id"]
        cli_session_id = existing["cli_session_id"]
    else:
        page_body: str | None = None
        repos: list[str] = []
        if req.wiki_path is not None:
            page_body, repos = _maybe_read_page_body(req.wiki_path, user)
        first_turn_prompt = prompt_builder.build_first_turn_prompt(
            wiki_path=req.wiki_path,
            page_body=page_body,
            working_dir=req.working_dir,
            linked_repos=repos,
            user_message=req.message,
        )
        wiki_path = req.wiki_path
        working_dir = req.working_dir
        machine_id = None
        cli_session_id = None

    # AF#14 — record the user's workdir choice if they ticked "remember".
    if req.remember_workdir_for_page and req.machine_id and wiki_path and working_dir:
        page_dirs.set_for_page(
            user_id=user.id,
            machine_id=req.machine_id,
            wiki_path=wiki_path,
            working_dir=working_dir,
        )

    sid = sessions_repo.create(
        user_id=user.id,
        tool_id=req.tool_id,
        first_turn_prompt=first_turn_prompt,
        wiki_path=wiki_path,
        working_dir=working_dir,
        machine_id=machine_id,
        cli_session_id=cli_session_id,
    )

    # Auto-mint a launcher MCP token (via launcher_tokens for plaintext).
    token_id, _ = launcher_tokens.get_or_mint_for_user(
        user.id,
        name=f"launcher-{req.tool_id}",
    )

    code = codes_repo.create(
        user_id=user.id,
        agent_session_id=sid,
        mcp_token_id=token_id,
    )

    endpoint = str(request.base_url).rstrip("/") + "/api/mcp"
    uri = f"agentwiki://run?code={code}&tool={req.tool_id}&endpoint={endpoint}"

    return LaunchResponse(launch_code=code, uri=uri, agent_session_id=sid)


# --------------------------------------------------------------------------- #
# POST /api/launch/exchange — helper-facing                                   #
# --------------------------------------------------------------------------- #


@router.post("/launch/exchange", response_model=ExchangeResponse)
def post_exchange(req: ExchangeRequest, request: Request) -> ExchangeResponse:
    _check_flag()
    consumed = codes_repo.consume(req.code)
    if consumed is None:
        raise HTTPException(status_code=404, detail="unknown launch code")
    if consumed == "already_consumed":
        raise HTTPException(status_code=409, detail="launch code already consumed")
    if consumed == "expired":
        raise HTTPException(status_code=410, detail="launch code expired")
    assert isinstance(consumed, dict)

    sess = sessions_repo.get(consumed["agent_session_id"])
    if sess is None:
        raise HTTPException(status_code=500, detail="agent_session missing")

    manifest = get_registry().get(sess["tool_id"])
    if manifest is None:
        raise HTTPException(status_code=500, detail="tool_id no longer recognized")

    # R5#1 — machine_id mismatch on resume = 409.
    if sess["machine_id"] is not None and sess["machine_id"] != req.machine_id:
        raise HTTPException(
            status_code=409,
            detail="session belongs to a different machine; start a new session",
        )

    sessions_repo.mark_active(sess["id"], machine_id=req.machine_id)

    raw_token = launcher_tokens.get_raw_for_token_id(consumed["mcp_token_id"])
    if raw_token is None:
        raise HTTPException(status_code=500, detail="launcher token plaintext missing")

    endpoint = str(request.base_url).rstrip("/") + "/api/mcp"
    is_resume = sess["cli_session_id"] is not None
    payload = ExchangePayload(
        session_id=sess["id"],
        working_dir=sess["working_dir"],
        first_turn_prompt=None if is_resume else sess["first_turn_prompt"],
        cli_session_id=sess["cli_session_id"] if is_resume else None,
    )

    return ExchangeResponse(
        mcp_token=raw_token,
        endpoint=endpoint,
        manifest=manifest.model_dump(mode="json", exclude_none=True),
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# Probe-ack / probe-status                                                    #
# --------------------------------------------------------------------------- #


_PROBE_TTL = 5.0
# nonce → (timestamp, helper_port, machine_id)
_probe_store: dict[str, tuple[float, int, str | None]] = {}
_probe_lock = RLock()


def _gc_probes() -> None:
    now = time()
    with _probe_lock:
        stale = [n for n, (ts, _, _) in _probe_store.items() if now - ts > _PROBE_TTL]
        for n in stale:
            del _probe_store[n]


@router.post("/launch/probe-ack")
def post_probe_ack(req: ProbeAckRequest) -> dict[str, bool]:
    _check_flag()
    _gc_probes()
    with _probe_lock:
        _probe_store[req.nonce] = (time(), req.helper_port, req.machine_id)
    return {"ok": True}


@router.get("/launch/probe-status", response_model=ProbeStatusResponse)
def get_probe_status(nonce: str) -> ProbeStatusResponse:
    _check_flag()
    _gc_probes()
    with _probe_lock:
        entry = _probe_store.get(nonce)
    if entry is None:
        return ProbeStatusResponse(acked=False)
    _, port, machine_id = entry
    return ProbeStatusResponse(acked=True, helper_port=port, machine_id=machine_id)
