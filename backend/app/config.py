"""Runtime configuration loaded from environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env so non-Docker launchers (python -m app.main, pytest,
# huey worker) get the same env as `flask run` and docker compose. Search
# upward from this file rather than relying on CWD.
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env")


@dataclass(frozen=True)
class Config:
    secret_key: str
    wiki_dir: str
    app_db_path: str
    queue_db_path: str
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


def load_config() -> Config:
    return Config(
        secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
        wiki_dir=os.environ.get("WIKI_DIR", "/wiki"),
        app_db_path=os.environ.get("APP_DB_PATH", "/data/app.sqlite"),
        queue_db_path=os.environ.get("QUEUE_DB_PATH", "/data/queue.sqlite"),
        max_queue_size=_positive_int("MAX_QUEUE_SIZE", 1000),
        auth_mode=os.environ.get("AUTH_MODE", "basic"),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
    )


CONFIG = load_config()
