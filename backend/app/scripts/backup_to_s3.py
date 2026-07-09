"""Back up the wiki to an S3-compatible bucket.

Produces one timestamped backup containing both halves of the workspace
state — they cross-reference each other and must be restored together:

- ``wiki-<ts>.bundle`` — a ``git bundle`` of the wiki repo (all refs, full
  history). Restore with ``git clone wiki-<ts>.bundle <dir>``.
- ``db-<ts>.dump`` — a ``pg_dump --format=custom`` of the app database
  (permissions, owners, update policies, comments, users, trigger cache).
  Restore with ``pg_restore``.

Backing up only the wiki volume silently loses all permissions and
comments (Postgres-only by design); backing up only Postgres loses the
pages. See ``docs/backups.md`` for the full restore procedure.

Uploads go to ``s3://<bucket>/<prefix>/<ts>/`` via boto3, so any
S3-compatible endpoint works (AWS S3, GCS interop, MinIO, Cloudflare R2).

Configuration is env-based (the Helm chart's backup CronJob sets these):

- ``BACKUP_S3_BUCKET``   — required, destination bucket.
- ``BACKUP_S3_PREFIX``   — key prefix, default ``agent-wiki-backups``.
- ``BACKUP_S3_ENDPOINT`` — optional endpoint URL for non-AWS backends.
- ``BACKUP_S3_REGION``   — optional region.
- ``BACKUP_KEEP_LAST``   — optional int; after a successful upload, delete
  all but the newest N backups under the prefix. Default 0 = keep
  everything (use bucket lifecycle rules instead if you prefer).
- Credentials via the standard AWS env vars / IAM role chain.

``DATABASE_URL`` and ``WIKI_DIR`` come from the normal app config.

Usage:

    python -m app.scripts.backup_to_s3
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.config import CONFIG
from app.utils.logging import setup_logging
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


class BackupConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    prefix: str = "agent-wiki-backups"
    endpoint_url: str | None = None
    region: str | None = None
    keep_last: int = 0

    @staticmethod
    def from_env() -> "BackupConfig":
        bucket = os.environ.get("BACKUP_S3_BUCKET", "")
        if not bucket:
            raise SystemExit("BACKUP_S3_BUCKET is required")
        return BackupConfig(
            bucket=bucket,
            prefix=os.environ.get("BACKUP_S3_PREFIX", "agent-wiki-backups"),
            endpoint_url=os.environ.get("BACKUP_S3_ENDPOINT") or None,
            region=os.environ.get("BACKUP_S3_REGION") or None,
            keep_last=int(os.environ.get("BACKUP_KEEP_LAST", "0")),
        )


def _s3_client(cfg: BackupConfig) -> Any:
    """``boto3.client`` behind an ``Any`` boundary — botocore is untyped, so
    the unavoidable Unknown is confined to this one seam."""
    import boto3

    return boto3.client(  # pyright: ignore
        "s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region
    )


def dump_database(dest_path: str) -> None:
    """``pg_dump --format=custom`` of the app database to ``dest_path``.

    Custom format so ``pg_restore`` can do selective/parallel restores and
    the dump is compressed. pg_dump takes a snapshot, so running against a
    live database is consistent.
    """
    # pg_dump takes libpq URIs (postgresql://); strip the SQLAlchemy dialect
    # marker in case the deployment set the +psycopg form.
    url = CONFIG.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--file",
            dest_path,
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def upload(client: Any, cfg: BackupConfig, ts: str, paths: list[Path]) -> list[str]:
    """Upload each file to ``s3://bucket/prefix/ts/<name>``; return the keys."""
    keys: list[str] = []
    for p in paths:
        key = f"{cfg.prefix}/{ts}/{p.name}"
        client.upload_file(str(p), cfg.bucket, key)
        head = client.head_object(Bucket=cfg.bucket, Key=key)
        log.info("uploaded s3://%s/%s (%d bytes)", cfg.bucket, key, head["ContentLength"])
        keys.append(key)
    return keys


def prune(client: Any, cfg: BackupConfig) -> list[str]:
    """Delete all but the newest ``keep_last`` backups under the prefix.

    Backups are grouped by their timestamp sub-prefix; timestamps are
    lexicographically sortable (UTC ``%Y%m%dT%H%M%SZ``), so newest = last.
    No-op when ``keep_last`` is 0.
    """
    if cfg.keep_last <= 0:
        return []
    resp = client.list_objects_v2(
        Bucket=cfg.bucket, Prefix=f"{cfg.prefix}/", Delimiter="/"
    )
    groups = sorted(cp["Prefix"] for cp in resp.get("CommonPrefixes", []))
    doomed = groups[: -cfg.keep_last] if len(groups) > cfg.keep_last else []
    deleted: list[str] = []
    for group in doomed:
        objs = client.list_objects_v2(Bucket=cfg.bucket, Prefix=group)
        for obj in objs.get("Contents", []):
            client.delete_object(Bucket=cfg.bucket, Key=obj["Key"])
            deleted.append(obj["Key"])
    if deleted:
        log.info("pruned %d object(s) beyond keep_last=%d", len(deleted), cfg.keep_last)
    return deleted


def run_backup(cfg: BackupConfig, client: Any) -> list[str]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix="agent-wiki-backup-") as tmp:
        bundle_path = Path(tmp) / f"wiki-{ts}.bundle"
        dump_path = Path(tmp) / f"db-{ts}.dump"
        wiki_git.bundle(str(bundle_path))
        log.info("wiki bundle: %d bytes", bundle_path.stat().st_size)
        dump_database(str(dump_path))
        log.info("db dump: %d bytes", dump_path.stat().st_size)
        keys = upload(client, cfg, ts, [bundle_path, dump_path])
    prune(client, cfg)
    return keys


def main() -> int:
    setup_logging()
    cfg = BackupConfig.from_env()
    try:
        keys = run_backup(cfg, _s3_client(cfg))
    except subprocess.CalledProcessError as e:
        log.error(
            "backup failed (exit %d): %s", e.returncode, (e.stderr or "").strip()
        )
        return 1
    except Exception:
        log.exception("backup failed")
        return 1
    log.info("backup complete: %s", ", ".join(keys))
    return 0


if __name__ == "__main__":
    sys.exit(main())
