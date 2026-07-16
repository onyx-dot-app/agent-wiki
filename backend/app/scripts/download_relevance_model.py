"""Download the two-tower relevance model (.onnx) from S3 to local disk.

Runs as an init-container on the ``documents`` worker before the app starts, so
the worker finds the warm model at ``INGEST_RELEVANCE_MODEL_PATH``. An empty
``INGEST_RELEVANCE_MODEL_S3_URI`` means there's nothing to fetch — the worker
then runs the cosine cold-start filter (see ``app.ingest.relevance.factory``).

Any S3-compatible endpoint works (AWS S3, GCS interop, MinIO, Cloudflare R2) via
boto3, same as the backup script. The download goes to a ``.part`` sibling and
is renamed into place only once complete, so a failed transfer never leaves a
truncated file that the scorer would try to load.

A download failure is logged but exits 0 (not a hard error): the worker still
starts and the factory falls back to cosine when the file is absent, keeping
ingestion up rather than crash-looping on an S3 blip. The degradation is visible
in the logs and in which filter the worker reports building.

Configuration is env-based (the Helm chart's init-container sets these):

- ``INGEST_RELEVANCE_MODEL_S3_URI`` — s3://bucket/key of the exported .onnx.
- ``INGEST_RELEVANCE_MODEL_PATH``   — local destination the app reads. Required.
- ``INGEST_RELEVANCE_MODEL_S3_ENDPOINT`` — optional endpoint URL for non-AWS.
- ``INGEST_RELEVANCE_MODEL_S3_REGION``   — optional region.
- Credentials via the standard AWS env vars / IAM role chain.

Usage:

    python -m app.scripts.download_relevance_model
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from app.utils.logging import setup_logging

log = logging.getLogger(__name__)


class ModelDownloadConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    s3_uri: str
    dest: str
    endpoint_url: str | None = None
    region: str | None = None

    @staticmethod
    def from_env() -> "ModelDownloadConfig":
        dest = os.environ.get("INGEST_RELEVANCE_MODEL_PATH", "")
        if not dest:
            raise SystemExit("INGEST_RELEVANCE_MODEL_PATH is required")
        return ModelDownloadConfig(
            s3_uri=os.environ.get("INGEST_RELEVANCE_MODEL_S3_URI", ""),
            dest=dest,
            endpoint_url=os.environ.get("INGEST_RELEVANCE_MODEL_S3_ENDPOINT") or None,
            region=os.environ.get("INGEST_RELEVANCE_MODEL_S3_REGION") or None,
        )


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/path/to/key`` into ``(bucket, key)``."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"not an s3:// URI: {uri!r}")
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"s3 URI has no object key: {uri!r}")
    return parsed.netloc, key


def _s3_client(cfg: ModelDownloadConfig) -> Any:
    """``boto3.client`` behind an ``Any`` boundary — botocore is untyped, so
    the unavoidable Unknown is confined to this one seam."""
    import boto3

    return boto3.client(  # pyright: ignore
        "s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region
    )


def download(client: Any, cfg: ModelDownloadConfig) -> None:
    """Fetch the model to a ``.part`` file, then rename it into ``cfg.dest``."""
    bucket, key = parse_s3_uri(cfg.s3_uri)
    dest = Path(cfg.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    client.download_file(bucket, key, str(part))  # pyright: ignore[reportUnknownMemberType]
    part.replace(dest)
    log.info("relevance model ready: s3://%s/%s -> %s (%d bytes)", bucket, key, dest, dest.stat().st_size)


def main() -> int:
    setup_logging()
    cfg = ModelDownloadConfig.from_env()
    if not cfg.s3_uri:
        log.info("INGEST_RELEVANCE_MODEL_S3_URI unset; skipping (cosine cold-start filter)")
        return 0
    try:
        download(_s3_client(cfg), cfg)
    except Exception:
        # Soft-fail: keep the worker startable on cosine rather than crash-loop.
        log.exception("relevance model download failed; worker will fall back to cosine")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
