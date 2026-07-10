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
Retention is the bucket's job: use a lifecycle rule on the prefix.

Configuration is env-based (the Helm chart's backup CronJob sets these):

- ``BACKUP_S3_BUCKET``   — required, destination bucket.
- ``BACKUP_S3_PREFIX``   — key prefix, default ``agent-wiki-backups``.
- ``BACKUP_S3_ENDPOINT`` — optional endpoint URL for non-AWS backends.
- ``BACKUP_S3_REGION``   — optional region.
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
from sqlalchemy.engine import make_url

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

    The URL is parsed with SQLAlchemy's ``make_url`` (handles any dialect
    marker, percent-encoded passwords, IPv6 hosts). The decoded password is
    passed via ``PGPASSWORD`` and removed from the URI so it never appears
    in the process table (argv is world-readable via
    ``/proc/<pid>/cmdline``).
    """
    url = make_url(CONFIG.database_url)
    env = None
    if url.password is not None:
        env = {**os.environ, "PGPASSWORD": str(url.password)}
        url = url._replace(password=None)
    url = url._replace(drivername="postgresql")
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--file",
            dest_path,
            url.render_as_string(hide_password=False),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
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


def run_backup(cfg: BackupConfig, client: Any) -> list[str]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix="agent-wiki-backup-") as tmp:
        bundle_path = Path(tmp) / f"wiki-{ts}.bundle"
        dump_path = Path(tmp) / f"db-{ts}.dump"
        # Bundle BEFORE dump — the order is load-bearing. The dump may only be
        # newer than the bundle: a page created in the gap restores as orphan
        # ACL rows (harmless). Flipped, it would restore as a page with no ACL
        # rows, which the implicit-public fallback in app/wiki/acl.py makes
        # readable by everyone.
        wiki_git.bundle(str(bundle_path))
        log.info("wiki bundle: %d bytes", bundle_path.stat().st_size)
        dump_database(str(dump_path))
        log.info("db dump: %d bytes", dump_path.stat().st_size)
        # Bundle is uploaded first for the same reason restores require both
        # files: a group missing db-*.dump is a failed run, not a backup.
        return upload(client, cfg, ts, [bundle_path, dump_path])


def main() -> int:
    setup_logging()
    cfg = BackupConfig.from_env()
    client = _s3_client(cfg)
    try:
        keys = run_backup(cfg, client)
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
