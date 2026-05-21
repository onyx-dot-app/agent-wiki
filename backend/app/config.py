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
    database_url: str
    redis_url: str
    opensearch_url: str
    opensearch_index: str
    max_queue_size: int

    auth_mode: str  # "basic" | "oidc"
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str

    # `True` when the app is served over HTTPS — toggles SESSION_COOKIE_SECURE.
    secure_cookies: bool

    # Ingest pipeline tuning
    ingest_bm25_min_score: float
    ingest_bm25_title_boost: float
    ingest_bm25_limit: int
    ingest_irrelevant_stop_n: int

    # Opt-in eval logging — captures reconciler inputs/outputs to ingest_eval_samples
    ingest_example_logging: bool

    # Coding-tool launchers (Run Agent button) — see
    # local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/.
    launchers_enabled: bool
    launch_code_ttl_seconds: int
    agent_session_idle_seconds: int
    agent_session_close_after_idle_seconds: int
    agent_session_spawn_ok_seconds: int


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a float, got {raw!r}") from e
    if value <= 0:
        raise ValueError(f"{name} must be a positive float, got {value}")
    return value


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
        redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
        opensearch_url=os.environ.get("OPENSEARCH_URL", "http://opensearch:9200"),
        opensearch_index=os.environ.get("OPENSEARCH_INDEX", "wiki-docs"),
        max_queue_size=_positive_int("MAX_QUEUE_SIZE", 1000),
        ingest_bm25_min_score=_positive_float("INGEST_BM25_MIN_SCORE", 1.0),
        ingest_bm25_title_boost=_positive_float("INGEST_BM25_TITLE_BOOST", 2.0),
        ingest_bm25_limit=_positive_int("INGEST_BM25_LIMIT", 20),
        ingest_irrelevant_stop_n=_positive_int("INGEST_IRRELEVANT_STOP_N", 2),
        auth_mode=os.environ.get("AUTH_MODE", "basic"),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        ingest_example_logging=os.environ.get("INGEST_EXAMPLE_LOGGING", "false").lower()
        in {"1", "true", "yes"},
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
        launchers_enabled=os.environ.get("LAUNCHERS_ENABLED", "false").lower()
        in {"1", "true", "yes"},
        launch_code_ttl_seconds=_positive_int("LAUNCH_CODE_TTL_SECONDS", 60),
        agent_session_idle_seconds=_positive_int("AGENT_SESSION_IDLE_SECONDS", 300),
        agent_session_close_after_idle_seconds=_positive_int(
            "AGENT_SESSION_CLOSE_AFTER_IDLE_SECONDS",
            86400,
        ),
        agent_session_spawn_ok_seconds=_positive_int(
            "AGENT_SESSION_SPAWN_OK_SECONDS",
            30,
        ),
    )


CONFIG = load_config()
