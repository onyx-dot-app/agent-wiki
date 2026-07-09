"""Auth endpoints — signup / login / logout / me / OIDC login + callback.

Session cookies are minted via Starlette's ``SessionMiddleware``
(installed in ``app.main``); setting ``request.session["user_id"]``
is all that's needed to "log in", and ``request.session.clear()`` to
log out.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError

from app.auth import User, users as users_repo
from app.auth.basic import authenticate
from app.auth.deps import require_user, user_epoch
from app.auth.oidc import client as oidc_client, upsert_oidc_user, SystemUserSignInError
from app.auth import invites
from app.auth.whitelist import is_allowed, is_open
from app.config import CONFIG
from app.models.auth import (
    AuthConfig,
    AuthSession,
    LoginRequest,
    OkResponse,
    SignupRequest,
)
from app.models.user_settings import UserSettings

router = APIRouter()
log = logging.getLogger(__name__)


def _session_payload(user: User) -> AuthSession:
    settings = users_repo.get_settings(user.id) or {}
    return AuthSession(
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
        settings=UserSettings.model_validate(settings),
    )


@router.post(
    "/signup",
    response_model=AuthSession,
    status_code=status.HTTP_201_CREATED,
)
def signup(req: SignupRequest, request: Request) -> AuthSession:
    if CONFIG.auth_mode != "basic":
        raise HTTPException(status_code=400, detail="signup disabled")
    email = req.email.strip().lower()
    name = (req.name or "").strip() or None
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    if not is_allowed(email) and not invites.is_invited(email):
        raise HTTPException(status_code=403, detail="email not allowed")
    if users_repo.get_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="account already exists")
    try:
        user_id = users_repo.create(email=email, password=req.password, name=name)
    except IntegrityError as exc:
        log.warning("signup race: account already exists for %s", email, exc_info=True)
        raise HTTPException(status_code=409, detail="account already exists") from exc
    # Consume the invite (if any) now that the account exists.
    invites.remove(email)
    row = users_repo.get_by_id(user_id)
    assert row is not None
    user = User(id=row["id"], email=row["email"], name=row["name"], is_admin=row["is_admin"])
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_epoch"] = user_epoch(user.id)
    log.info("signup: user %s (%s) is_admin=%s", user.id, user.email, user.is_admin)
    return _session_payload(user)


@router.post("/login", response_model=AuthSession)
def login(req: LoginRequest, request: Request) -> AuthSession:
    if CONFIG.auth_mode != "basic":
        raise HTTPException(status_code=400, detail="basic auth disabled")
    email = req.email.strip()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    user = authenticate(email, req.password)
    if user is None:
        log.warning("login failed for %s", email)
        raise HTTPException(status_code=401, detail="invalid credentials")
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_epoch"] = user_epoch(user.id)
    log.info("login: user %s (%s)", user.id, user.email)
    return _session_payload(user)


@router.post("/logout", response_model=OkResponse)
def logout(request: Request) -> OkResponse:
    request.session.clear()
    return OkResponse()


@router.get("/oidc/login")
async def oidc_login(request: Request) -> Response:
    """Kick off the OIDC authorization-code flow."""
    if CONFIG.auth_mode != "oidc":
        raise HTTPException(status_code=400, detail="oidc disabled")
    client = oidc_client()
    if client is None:
        raise HTTPException(status_code=503, detail="oidc not configured")
    redirect_uri = CONFIG.oidc_redirect_uri or str(
        request.url_for("oidc_callback"),
    )
    return cast(
        Response,
        await client.authorize_redirect(  # pyright: ignore[reportUnknownMemberType]
            request, redirect_uri
        ),
    )


@router.get("/oidc/callback", name="oidc_callback")
async def oidc_callback(request: Request) -> Response:
    """OIDC redirect handler — exchanges code for token, upserts user,
    starts session."""
    if CONFIG.auth_mode != "oidc":
        raise HTTPException(status_code=400, detail="oidc disabled")
    client = oidc_client()
    if client is None:
        raise HTTPException(status_code=503, detail="oidc not configured")
    try:
        token = cast(
            "dict[str, Any]",
            await client.authorize_access_token(request),  # pyright: ignore[reportUnknownMemberType]
        )
    except Exception:
        log.exception("oidc: failed to exchange authorization code")
        return RedirectResponse(url="/login?error=oidc_exchange_failed")

    userinfo_raw = token.get("userinfo")
    if userinfo_raw is None:
        try:
            userinfo_raw = await client.userinfo(token=token)  # pyright: ignore[reportUnknownMemberType]
        except Exception:
            log.exception("oidc: failed to fetch userinfo")
            return RedirectResponse(url="/login?error=oidc_userinfo_failed")
    userinfo = cast("dict[str, Any]", userinfo_raw)

    email = (userinfo.get("email") or "").strip().lower()
    if not email:
        log.warning("oidc: userinfo missing email; payload keys=%s", list(userinfo.keys()))
        return RedirectResponse(url="/login?error=oidc_no_email")
    if userinfo.get("email_verified") is False:
        log.warning("oidc: email not verified for %s", email)
        return RedirectResponse(url="/login?error=oidc_email_unverified")
    if not is_allowed(email) and not invites.is_invited(email):
        log.info("oidc: email %s not in allow list", email)
        return RedirectResponse(url="/login?error=oidc_email_not_allowed")

    name_raw = userinfo.get("name")
    name = name_raw if isinstance(name_raw, str) else None
    try:
        user_id = upsert_oidc_user(email=email, name=name)
    except SystemUserSignInError:
        log.warning("oidc: refused sign-in as system user %s", email)
        return RedirectResponse(url="/login?error=oidc_email_not_allowed")
    invites.remove(email)
    row = users_repo.get_by_id(user_id)
    assert row is not None
    user = User(id=row["id"], email=row["email"], name=row["name"], is_admin=row["is_admin"])
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_epoch"] = user_epoch(user.id)
    log.info("oidc login: user %s (%s)", user.id, user.email)
    return RedirectResponse(url="/")


@router.get("/me", response_model=AuthSession)
def me(user: User = Depends(require_user)) -> AuthSession:
    return _session_payload(user)


@router.get("/config", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    """Public — frontend uses this to know whether to show the signup form."""
    return AuthConfig(mode=CONFIG.auth_mode, signup_open=is_open())
