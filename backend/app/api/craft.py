"""HTTP API for Onyx Craft launches + the Connect-Onyx account link.

Routes mounted under ``/api/craft`` from ``app.main:create_app``:

- ``POST /api/craft/launch``            — idempotent launch (enqueues the worker)
- ``GET  /api/craft/connect``           — connection status for the current user
- ``POST /api/craft/connect``           — manual-PAT connect (v0): validate + store
- ``DELETE /api/craft/connect``         — disconnect (best-effort revoke on Onyx)
- ``GET  /api/craft/connect/start``     — redirect-mint connect (dormant; awaits Onyx Phase 1)
- ``GET  /api/craft/connect/callback``  — redirect-mint return leg (dormant)

Connect (v0) is **manual PAT paste**: the user mints an Onyx Personal Access
Token and pastes it; we validate via ``GET /api/me`` and store it per-user.
Each user's PAT means their Craft runs execute as them, so their Onyx
knowledge ACLs + LLM access are respected. The redirect-mint flow
(``connect/start`` + ``connect/callback``) is the future no-copy-paste UX and
stays dormant until the Onyx ``/connect/agent-wiki`` endpoints ship.

Gated on an admin-configured Onyx base URL (``ingest_settings.onyx_base_url``,
the "Onyx Connection" admin page) — availability is computed from config, not a
deploy flag, so the feature merges dark. See "Engineering Projects/Craft Integration".
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.auth import User
from app.auth.deps import require_user
from app.config import CONFIG
from app.ingest import settings as ingest_settings
from app.launchers import craft as craft_workflow
from app.models.craft import (
    CraftConnectRequest,
    CraftConnectStatus,
    CraftLaunchRequest,
    CraftLaunchResponse,
)
from app.onyx import connect as connect_flow
from app.onyx import connections
from app.onyx.client import OnyxAuthError, OnyxClient, OnyxError, exchange_connect_code
from app.tasks.queues import QueueFullError

log = logging.getLogger(__name__)

router = APIRouter()


def _require_available() -> str:
    """404 when the feature is dark; otherwise the Onyx origin."""
    try:
        return craft_workflow.require_available()
    except craft_workflow.CraftUnavailable as exc:
        raise HTTPException(status_code=404, detail="onyx connection not configured") from exc


def _callback_url() -> str:
    return f"{CONFIG.public_base_url}/api/craft/connect/callback"


def _connect_start_url(return_to: str | None) -> str:
    url = f"{CONFIG.public_base_url}/api/craft/connect/start"
    if return_to:
        url += f"?return_to={quote(return_to, safe='/')}"
    return url


def _bounce(return_to: str | None, *, outcome: str) -> RedirectResponse:
    """Send the browser back into the app after the connect flow."""
    path = connect_flow.normalize_return_to(return_to) or "/"
    sep = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{CONFIG.public_base_url}{path}{sep}onyx_connect={outcome}", status_code=302
    )


# --------------------------------------------------------------------------- #
# POST /api/craft/launch                                                      #
# --------------------------------------------------------------------------- #


@router.post("/launch", response_model=CraftLaunchResponse)
def post_launch(req: CraftLaunchRequest, user: User = Depends(require_user)) -> CraftLaunchResponse:
    """Thin HTTP shell over the shared launch workflow: translate its
    errors to status codes."""
    try:
        sid, status = craft_workflow.start_session(
            user_id=user.id,
            is_admin=bool(user.is_admin),
            wiki_path=req.wiki_path,
            message=req.message,
        )
    except craft_workflow.CraftUnavailable as exc:
        raise HTTPException(status_code=404, detail="onyx connection not configured") from exc
    except craft_workflow.CraftMisconfigured as exc:
        raise HTTPException(
            status_code=500, detail="onyx-craft manifest missing or not in_app"
        ) from exc
    except craft_workflow.CraftNotConnected as exc:
        # Structured signal, not an error state — the frontend renders a
        # "Connect Onyx" call-to-action and sends the browser to /connect/start.
        raise HTTPException(status_code=409, detail="needs_onyx_connect") from exc
    except craft_workflow.CraftInvalidPath as exc:
        raise HTTPException(status_code=400, detail="invalid wiki_path") from exc
    except craft_workflow.CraftForbidden as exc:
        raise HTTPException(status_code=403, detail="forbidden") from exc
    except craft_workflow.CraftRateLimited as exc:
        raise HTTPException(status_code=429, detail="rate_limited") from exc
    except QueueFullError:
        raise
    except Exception as exc:
        log.exception("craft launch enqueue failed user=%s", user.id)
        raise HTTPException(status_code=503, detail="failed to enqueue craft launch") from exc
    return CraftLaunchResponse(agent_session_id=sid, status=status)


# --------------------------------------------------------------------------- #
# Connect-Onyx                                                                #
# --------------------------------------------------------------------------- #


@router.get("/connect", response_model=CraftConnectStatus)
def get_connect_status(
    return_to: str | None = None,
    user: User = Depends(require_user),
) -> CraftConnectStatus:
    base = _require_available()
    row = connections.status(user.id)
    connected = row is not None and row["onyx_base_url"] == base
    return CraftConnectStatus(
        connected=connected,
        onyx_user_email=row["onyx_user_email"] if connected and row else None,
        token_hint=row["token_display"] if connected and row else None,
        expires_at=row["expires_at"] if connected and row else None,
        onyx_base_url=base,
        connect_url=_connect_start_url(return_to),
    )


@router.post("/connect", response_model=CraftConnectStatus)
def connect_with_pat(
    req: CraftConnectRequest,
    user: User = Depends(require_user),
) -> CraftConnectStatus:
    """Manual-PAT connect (v0). Validate the pasted token against the
    configured Onyx instance, then store it as this user's credential."""
    base = _require_available()
    pat = req.pat.strip()
    client = OnyxClient(base, pat)  # also re-validates the base URL
    try:
        me = client.whoami()
    except OnyxAuthError as exc:
        raise HTTPException(status_code=401, detail="invalid_onyx_pat") from exc
    except OnyxError as exc:
        raise HTTPException(status_code=502, detail="onyx_unreachable") from exc
    connections.upsert(
        user_id=user.id,
        onyx_pat=pat,
        onyx_user_email=me.get("email"),
        expires_at=None,  # manual PATs carry no expiry hint; re-connect on 401
        onyx_base_url=base,
    )
    row = connections.status(user.id)
    return CraftConnectStatus(
        connected=True,
        onyx_user_email=row["onyx_user_email"] if row else me.get("email"),
        token_hint=row["token_display"] if row else None,
        expires_at=row["expires_at"] if row else None,
        onyx_base_url=base,
        connect_url=None,
    )


@router.get("/connect/start")
def connect_start(
    return_to: str | None = None,
    user: User = Depends(require_user),
) -> RedirectResponse:
    base = _require_available()
    state, code_challenge = connect_flow.mint_state(user_id=user.id, return_to=return_to)
    return RedirectResponse(
        connect_flow.build_authorize_url(
            base,
            redirect_uri=_callback_url(),
            state=state,
            code_challenge=code_challenge,
        ),
        status_code=302,
    )


@router.get("/connect/callback")
def connect_callback(
    code: str,
    state: str,
    user: User = Depends(require_user),
) -> RedirectResponse:
    base = _require_available()
    claimed = connect_flow.consume_state(state, user_id=user.id)
    if claimed is None:
        log.warning("craft connect callback rejected (bad state) user=%s", user.id)
        return _bounce(None, outcome="error")
    return_to = claimed["return_to"]
    try:
        body = exchange_connect_code(base, code=code, code_verifier=claimed["code_verifier"])
    except OnyxError:
        log.exception("craft connect exchange failed user=%s", user.id)
        return _bounce(return_to, outcome="error")
    connections.upsert(
        user_id=user.id,
        onyx_pat=body["pat"],
        onyx_user_email=body.get("onyx_user_email"),
        expires_at=body.get("expires_at"),
        onyx_base_url=base,
    )
    return _bounce(return_to, outcome="ok")


@router.delete("/connect")
def disconnect(user: User = Depends(require_user)) -> dict[str, bool]:
    base = ingest_settings.get_onyx_base_url()
    row = connections.get_with_pat(user.id, onyx_base_url=base) if base else None
    if row is not None:
        try:
            OnyxClient(row["onyx_base_url"], row["onyx_pat"]).revoke_pat()
        except OnyxError:
            log.warning("craft disconnect: best-effort revoke failed user=%s", user.id)
    removed = connections.remove(user.id)
    return {"disconnected": removed}
