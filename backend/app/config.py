"""Runtime configuration loaded from environment."""
from __future__ import annotations

import os
from dataclasses import dataclass


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

    llm_provider: str  # "anthropic" | "openai"
    llm_model: str
    anthropic_api_key: str
    openai_api_key: str


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
        llm_provider=os.environ.get("LLM_PROVIDER", "anthropic"),
        llm_model=os.environ.get("LLM_MODEL", "claude-opus-4-7"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
    )


CONFIG = load_config()
