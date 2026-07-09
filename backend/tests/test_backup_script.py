"""Tests for the S3 backup script (`app/scripts/backup_to_s3.py`).

The git bundle path runs against a real tmp repo (never mock git). pg_dump
is stubbed — dev machines and CI carry arbitrary client versions — and S3
is a recording fake so the key layout and pruning behavior are asserted
without network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.scripts import backup_to_s3
from app.scripts.backup_to_s3 import BackupConfig, prune, run_backup
from app.wiki import git as wiki_git


class FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client surface we use."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.objects[key] = Path(filename).read_bytes()

    def head_object(self, Bucket: str, Key: str) -> dict:
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, Bucket: str, Prefix: str, Delimiter: str | None = None) -> dict:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if Delimiter is None:
            return {"Contents": [{"Key": k} for k in keys]}
        groups = sorted({k[: k.index(Delimiter, len(Prefix)) + 1] for k in keys})
        return {"CommonPrefixes": [{"Prefix": g} for g in groups]}

    def delete_object(self, Bucket: str, Key: str) -> None:
        del self.objects[Key]


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


def test_run_backup_uploads_bundle_and_dump(backup_env, monkeypatch):
    monkeypatch.setattr(
        backup_to_s3,
        "dump_database",
        lambda dest: Path(dest).write_bytes(b"fake-dump"),
    )
    client = FakeS3()
    cfg = BackupConfig(bucket="b", prefix="wiki-backups")

    keys = run_backup(cfg, client)

    assert len(keys) == 2
    names = sorted(k.rsplit("/", 1)[1] for k in keys)
    assert names[0].startswith("db-") and names[0].endswith(".dump")
    assert names[1].startswith("wiki-") and names[1].endswith(".bundle")
    # Both land under the same timestamp group: wiki-backups/<ts>/<name>.
    groups = {k.rsplit("/", 2)[1] for k in keys}
    assert len(groups) == 1
    assert client.objects[keys[0]]  # non-empty payloads made it up
    assert client.objects[keys[1]]


def test_prune_keeps_newest_groups(backup_env):
    client = FakeS3()
    for ts in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        client.objects[f"p/{ts}/wiki-{ts}.bundle"] = b"x"
        client.objects[f"p/{ts}/db-{ts}.dump"] = b"y"

    deleted = prune(client, BackupConfig(bucket="b", prefix="p", keep_last=2))

    assert sorted(deleted) == [
        "p/20260101T000000Z/db-20260101T000000Z.dump",
        "p/20260101T000000Z/wiki-20260101T000000Z.bundle",
    ]
    remaining_groups = {k.split("/")[1] for k in client.objects}
    assert remaining_groups == {"20260102T000000Z", "20260103T000000Z"}


def test_prune_disabled_by_default(backup_env):
    client = FakeS3()
    client.objects["p/20260101T000000Z/wiki.bundle"] = b"x"
    assert prune(client, BackupConfig(bucket="b", prefix="p")) == []
    assert client.objects


def test_dump_database_normalizes_sqlalchemy_url(backup_env, monkeypatch, tmp_config):
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    cfg = tmp_config.model_copy(
        update={
            "database_url": tmp_config.database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        }
    )
    monkeypatch.setattr("app.scripts.backup_to_s3.CONFIG", cfg)
    monkeypatch.setattr(backup_to_s3.subprocess, "run", fake_run)

    backup_to_s3.dump_database("/tmp/out.dump")

    url_arg = seen[0][-1]
    assert url_arg.startswith("postgresql://")
    assert "+psycopg" not in url_arg
