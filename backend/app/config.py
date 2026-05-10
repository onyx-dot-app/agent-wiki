"""Runtime configuration loaded from environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

# Load the repo-root .env so non-Docker launchers (python -m app.main, pytest,
# task workers) get the same env as `flask run` and docker compose. Search
# upward from this file rather than relying on CWD.
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env")


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    secret_key: str
    wiki_dir: str
    database_url: str          # Postgres connection string for app state + pgmq queues
    max_queue_size: int

    auth_mode: str  # "basic" | "oidc"
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str

    # `True` when the app is served over HTTPS — toggles SESSION_COOKIE_SECURE.
    secure_cookies: bool


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value}")
    return value


def _resolve_wiki_dir() -> str:
    # Anchor relative paths against the repo root so the resolved location
    # doesn't depend on which directory the process was launched from
    # (`backend/`, repo root, pytest, etc.). Absolute paths pass through.
    raw = os.environ.get("WIKI_DIR", "/wiki")
    p = Path(raw)
    if not p.is_absolute():
        p = (_repo_root / p).resolve()
    return str(p)


def load_config() -> Config:
    return Config(
        secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
        wiki_dir=_resolve_wiki_dir(),
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@postgres:5432/agent_wiki",
        ),
        max_queue_size=_positive_int("MAX_QUEUE_SIZE", 1000),
        auth_mode=os.environ.get("AUTH_MODE", "basic"),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
    )


CONFIG = load_config()
