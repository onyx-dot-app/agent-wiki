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

    auth_mode: str  # "basic" | "oidc"
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str

    # `True` when the app is served over HTTPS — toggles SESSION_COOKIE_SECURE.
    secure_cookies: bool


def load_config() -> Config:
    return Config(
        secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
        wiki_dir=os.environ.get("WIKI_DIR", "/wiki"),
        app_db_path=os.environ.get("APP_DB_PATH", "/data/app.sqlite"),
        queue_db_path=os.environ.get("QUEUE_DB_PATH", "/data/queue.sqlite"),
        auth_mode=os.environ.get("AUTH_MODE", "basic"),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
    )


CONFIG = load_config()
