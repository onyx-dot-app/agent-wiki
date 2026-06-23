"""Runtime configuration loaded from environment."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

log = logging.getLogger(__name__)

# Built-in fallback used only for local dev. A production deployment must
# override it — see verify_secret_key().
DEV_SECRET_KEY = "dev-secret"

# Minimum length for an explicitly-set ENCRYPTION_KEY_SECRET (`openssl rand
# -hex 32` yields 64). Below this it's too weak to derive an at-rest key from.
_MIN_ENCRYPTION_KEY_LEN = 32

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

    # Dedicated secret for at-rest column encryption (app/db/crypto.py). Empty =
    # fall back to ``secret_key`` (the historical behavior), so existing
    # ciphertext keeps decrypting. When set it must be a real key (>= 32 chars,
    # enforced in verify_secret_key()). Set it to rotate the encryption key
    # independently of the cookie-signing key — see app/scripts/rotate_encryption_key.py.
    encryption_key_secret: str

    # `True` only in local dev / CI. Production must leave it false (the
    # default) — it's the opt-in that downgrades verify_secret_key() from
    # fatal to a warning.
    dev_mode: bool

    # Ingest pipeline tuning
    ingest_bm25_min_score: float
    ingest_bm25_title_boost: float
    ingest_bm25_limit: int
    ingest_irrelevant_stop_n: int

    # Opt-in eval logging — captures reconciler inputs/outputs to ingest_eval_samples
    ingest_eval_logging: bool
    # Public-facing wiki origin (e.g. "https://dev-wiki.onyx.app" or
    # "http://localhost:3088"). REQUIRED — set via the PUBLIC_BASE_URL
    # env var. Single source of truth for the browser-facing URL the
    # backend advertises in launch URIs, redirect targets, email
    # links, etc. Never inferred from request headers — header sniffing
    # behind a proxy is fragile and a spoofing surface; explicit
    # operator config is the enterprise pattern (cf. GitLab
    # external_url, Sentry system.url-prefix, Mattermost SiteURL).
    public_base_url: str

    # Coding-tool launchers (Run Agent button) — see
    # local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/.
    launchers_enabled: bool
    launch_code_ttl_seconds: int
    agent_session_idle_seconds: int
    agent_session_close_after_idle_seconds: int
    agent_session_spawn_ok_seconds: int

    @field_validator("public_base_url")
    @classmethod
    def _validate_public_base_url(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "PUBLIC_BASE_URL is required (e.g. "
                "https://wiki.example.com or http://localhost:3088). "
                "Set it on every deployment — the backend advertises it "
                "to browsers and the launcher helper."
            )
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"PUBLIC_BASE_URL must start with http:// or https:// (got {v!r})")
        if v.endswith("/"):
            raise ValueError(f"PUBLIC_BASE_URL must not have a trailing slash (got {v!r})")
        return v


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
        secret_key=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        wiki_dir=_resolve_wiki_dir(),
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@postgres:5432/agent_wiki",
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
        opensearch_url=os.environ.get("OPENSEARCH_URL", "http://opensearch:9200"),
        opensearch_index=os.environ.get("OPENSEARCH_INDEX", "wiki-docs"),
        max_queue_size=_positive_int("MAX_QUEUE_SIZE", 1000),
        ingest_bm25_min_score=_positive_float("INGEST_BM25_MIN_SCORE", 5.0),
        ingest_bm25_title_boost=_positive_float("INGEST_BM25_TITLE_BOOST", 2.0),
        ingest_bm25_limit=_positive_int("INGEST_BM25_LIMIT", 100),
        ingest_irrelevant_stop_n=_positive_int("INGEST_IRRELEVANT_STOP_N", 2),
        auth_mode=os.environ.get("AUTH_MODE", "basic"),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        ingest_eval_logging=os.environ.get("INGEST_EVAL_LOGGING", "false").lower()
        in {"1", "true", "yes"},
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
        dev_mode=os.environ.get("DEV_MODE", "false").lower() in {"1", "true", "yes"},
        encryption_key_secret=os.environ.get("ENCRYPTION_KEY_SECRET", ""),
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
        public_base_url=os.environ.get("PUBLIC_BASE_URL", ""),  # validated
    )


CONFIG = load_config()


def verify_secret_key(config: Config | None = None) -> None:
    """Validate the secrets that protect cookies and at-rest encryption.

    SECRET_KEY signs session cookies and (by default) derives the AES key that
    encrypts the secret columns at rest (app/db/crypto.py); the built-in default
    is public, so it must not be used on a real deployment. ENCRYPTION_KEY_SECRET
    is optional, but when set it derives the at-rest key, so a weak value
    silently undermines encryption even when SECRET_KEY is fine — require it to
    be long enough to be a real key.

    Production (``dev_mode`` off, the default) treats a violation as fatal;
    local dev / CI opt out with ``DEV_MODE=true``. Called at startup before
    init_db() so a misconfigured prod fails fast rather than encrypting live
    data under a weak/public key.
    """
    cfg = config or CONFIG

    def _enforce(ok: bool, message: str) -> None:
        if ok:
            return
        if not cfg.dev_mode:
            raise ValueError(message)
        log.warning("%s Allowed because DEV_MODE is set.", message)

    _enforce(
        bool(cfg.secret_key) and cfg.secret_key != DEV_SECRET_KEY,
        "SECRET_KEY is unset or the built-in default. It signs session cookies "
        "and derives the at-rest encryption key, so the public default makes "
        "cookies forgeable and encrypted secrets readable. Generate one with "
        "`openssl rand -hex 32` and set SECRET_KEY.",
    )
    # Optional (falls back to SECRET_KEY), but if an operator sets it, a short
    # value would silently weaken at-rest encryption — hold it to a real key.
    _enforce(
        not cfg.encryption_key_secret
        or len(cfg.encryption_key_secret) >= _MIN_ENCRYPTION_KEY_LEN,
        f"ENCRYPTION_KEY_SECRET is set but shorter than {_MIN_ENCRYPTION_KEY_LEN} "
        "characters; it derives the at-rest encryption key. Generate one with "
        "`openssl rand -hex 32`.",
    )
