"""Tests for the S3 backup script (`app/scripts/backup_to_s3.py`).

The git bundle path runs against a real tmp repo (never mock git). pg_dump
is stubbed — dev machines and CI carry arbitrary client versions — and S3
is a recording fake so upload behavior is asserted without network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.scripts import backup_to_s3
from app.scripts.backup_to_s3 import BackupConfig, run_backup
from app.wiki import git as wiki_git


class FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client surface we use."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.objects[key] = Path(filename).read_bytes()

    def head_object(self, Bucket: str, Key: str) -> dict:
        return {"ContentLength": len(self.objects[Key])}


@pytest.fixture
def backup_env(tmp_repo, tmp_config, monkeypatch):
    """Wiki repo with one committed page + CONFIG patched into the script."""
    monkeypatch.setattr("app.scripts.backup_to_s3.CONFIG", tmp_config)
    wiki_git.commit_file("Backup Test.md", "# hello backup\n", "seed", author=None)
    return tmp_config


def test_bundle_round_trips_through_git_clone(backup_env, tmp_path):
    bundle_path = tmp_path / "wiki.bundle"
    wiki_git.bundle(str(bundle_path))

    clone_dir = tmp_path / "restored"
    subprocess.run(
        ["git", "clone", str(bundle_path), str(clone_dir)],
        check=True,
        capture_output=True,
    )
    assert (clone_dir / "Backup Test.md").read_text() == "# hello backup\n"


def test_run_backup_uploads_bundle_then_dump(backup_env, monkeypatch):
    monkeypatch.setattr(
        backup_to_s3,
        "dump_database",
        lambda dest: Path(dest).write_bytes(b"fake-dump"),
    )
    client = FakeS3()
    cfg = BackupConfig(bucket="b", prefix="wiki-backups")

    keys = run_backup(cfg, client)

    # Bundle first — a group missing db-*.dump must read as a failed run.
    assert len(keys) == 2
    assert keys[0].rsplit("/", 1)[1].startswith("wiki-")
    assert keys[0].endswith(".bundle")
    assert keys[1].rsplit("/", 1)[1].startswith("db-")
    assert keys[1].endswith(".dump")
    # Both land under the same timestamp group: wiki-backups/<ts>/<name>.
    groups = {k.rsplit("/", 2)[1] for k in keys}
    assert len(groups) == 1
    assert client.objects[keys[0]]  # non-empty payloads made it up
    assert client.objects[keys[1]]


def test_dump_database_normalizes_url_and_hides_password(
    backup_env, monkeypatch, tmp_config
):
    seen: list[tuple[list[str], dict | None]] = []

    def fake_run(args, **kwargs):
        seen.append((args, kwargs.get("env")))
        return subprocess.CompletedProcess(args, 0, "", "")

    cfg = tmp_config.model_copy(
        update={
            "database_url": "postgresql+psycopg://wiki:s3%40cr%3At@db.example:5433/agent_wiki?options=-csearch_path%3Dfoo"
        }
    )
    monkeypatch.setattr("app.scripts.backup_to_s3.CONFIG", cfg)
    monkeypatch.setattr(backup_to_s3.subprocess, "run", fake_run)

    backup_to_s3.dump_database("/tmp/out.dump")

    args, env = seen[0]
    url_arg = args[-1]
    # Dialect marker stripped; password absent from argv (world-readable via
    # /proc); PGPASSWORD carries the DECODED password; query params survive.
    assert url_arg.startswith("postgresql://wiki@db.example:5433/agent_wiki")
    assert "s3%40" not in url_arg and "s3@" not in url_arg
    assert "options=-csearch_path" in url_arg
    assert env is not None and env["PGPASSWORD"] == "s3@cr:t"


def test_dump_database_handles_ipv6_host(backup_env, monkeypatch, tmp_config):
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    cfg = tmp_config.model_copy(
        update={"database_url": "postgresql://wiki:pw@[::1]:5433/agent_wiki"}
    )
    monkeypatch.setattr("app.scripts.backup_to_s3.CONFIG", cfg)
    monkeypatch.setattr(backup_to_s3.subprocess, "run", fake_run)

    backup_to_s3.dump_database("/tmp/out.dump")

    assert seen[0][-1] == "postgresql://wiki@[::1]:5433/agent_wiki"
