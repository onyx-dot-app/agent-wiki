"""OpenAI embeddings for the ingestion relevance filter (Phase 0).

Best-effort, like ``app.db.fts``: any failure logs at WARNING and returns
``None`` so an embedding glitch never aborts a doc commit or the reindex
sweep. The OpenAI key is the admin-configured one in ``llm_settings`` (no env
fallback), matching the chat providers.

Vectors are ``text-embedding-3-small`` (1536-d), content-capped to match the
offline model-selection study so production vectors are distributed like the
vectors the cosine / model thresholds were calibrated on. Gated on the OpenAI
key alone (:func:`available`): a deployment without one is a no-op, so this
module changes no behavior until an OpenAI provider is configured.
"""
from __future__ import annotations

import hashlib
import logging
from array import array
from functools import lru_cache

from openai import OpenAI

from app.config import CONFIG
from app.llm import settings as llm_settings

log = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

# Char caps mirror the eval so production vectors match the calibrated
# thresholds: incoming doc content ~8k, wiki page body ~24k.
DOC_CHAR_CAP = 8_000
PAGE_CHAR_CAP = 24_000

_BATCH = 128


def model_name() -> str:
    return CONFIG.ingest_embed_model or DEFAULT_MODEL


def content_sha256(text: str) -> str:
    """Stable content hash — the re-embed guard (skip unchanged bodies)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(vec: list[float]) -> bytes:
    """Pack a float vector to bytes for the ``bytea`` column (float32)."""
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> list[float]:
    """Inverse of :func:`pack`."""
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


@lru_cache(maxsize=4)
def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def _api_key() -> str:
    try:
        return llm_settings.get().openai_api_key or ""
    except Exception:
        log.warning("embeddings: could not read llm_settings", exc_info=True)
        return ""


def available() -> bool:
    """True when an OpenAI key is configured. Embeddings are gated on the key
    alone — there's no separate enable flag; a deployment with an OpenAI key
    (the same one the chat providers use) gets page/doc embeddings, one without
    is a no-op."""
    return bool(_api_key())


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts.

    Returns one vector per input (same order), or ``None`` on any failure and
    when no OpenAI key is configured. Never raises — callers treat ``None`` as
    "no vector this time" and fall back (stale vector / skip).
    """
    if not texts:
        return None
    key = _api_key()
    if not key:
        return None
    client = _client(key)
    model = model_name()
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), _BATCH):
            # OpenAI rejects empty strings; substitute a space.
            chunk = [t if t else " " for t in texts[i : i + _BATCH]]
            resp = client.embeddings.create(model=model, input=chunk)
            out.extend(list(d.embedding) for d in resp.data)
        return out
    except Exception:
        log.warning("embeddings: embed_texts failed for %d text(s)", len(texts), exc_info=True)
        return None


def embed_text(text: str) -> list[float] | None:
    """Embed a single text. ``None`` on failure / disabled / unconfigured."""
    res = embed_texts([text])
    return res[0] if res else None
