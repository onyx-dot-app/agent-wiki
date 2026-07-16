"""Tests for the relevance-model download script.

S3 is an in-memory recording fake (no network); the URI parsing and the
temp-then-rename / soft-fail behavior are what's under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scripts import download_relevance_model as dl
from app.scripts.download_relevance_model import (
    ModelDownloadConfig,
    download,
    parse_s3_uri,
)


class FakeS3:
    """Minimal stand-in for the boto3 S3 client surface we use."""

    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        Path(filename).write_bytes(self.objects[(bucket, key)])


def test_parse_s3_uri_splits_bucket_and_key():
    assert parse_s3_uri("s3://my-bucket/models/relevance/model.onnx") == (
        "my-bucket",
        "models/relevance/model.onnx",
    )


@pytest.mark.parametrize("bad", ["https://x/y", "s3://bucket", "s3:///key", "model.onnx"])
def test_parse_s3_uri_rejects_bad(bad: str):
    with pytest.raises(ValueError):
        parse_s3_uri(bad)


def test_download_writes_dest_and_leaves_no_part(tmp_path: Path):
    dest = tmp_path / "sub" / "model.onnx"  # parent doesn't exist yet
    client = FakeS3({("b", "k/model.onnx"): b"onnx-bytes"})
    cfg = ModelDownloadConfig(s3_uri="s3://b/k/model.onnx", dest=str(dest))

    download(client, cfg)

    assert dest.read_bytes() == b"onnx-bytes"
    assert not dest.with_suffix(".onnx.part").exists()  # renamed, not left behind


def test_failed_download_leaves_no_partial_file(tmp_path: Path):
    dest = tmp_path / "model.onnx"

    class Boom:
        def download_file(self, *a: object, **k: object) -> None:
            Path(a[2] if len(a) > 2 else k["filename"]).write_bytes(b"partial")  # type: ignore[index]
            raise RuntimeError("network died mid-transfer")

    cfg = ModelDownloadConfig(s3_uri="s3://b/k.onnx", dest=str(dest))
    with pytest.raises(RuntimeError):
        download(Boom(), cfg)
    # The truncated .part never got promoted to dest.
    assert not dest.exists()


def test_main_soft_fails_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INGEST_RELEVANCE_MODEL_PATH", str(tmp_path / "model.onnx"))
    monkeypatch.setenv("INGEST_RELEVANCE_MODEL_S3_URI", "s3://b/k.onnx")
    monkeypatch.setattr(dl, "_s3_client", lambda cfg: (_ for _ in ()).throw(RuntimeError("no creds")))
    # A download failure must not crash the worker init — exit 0, run cosine.
    assert dl.main() == 0


def test_main_skips_when_uri_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INGEST_RELEVANCE_MODEL_PATH", str(tmp_path / "model.onnx"))
    monkeypatch.delenv("INGEST_RELEVANCE_MODEL_S3_URI", raising=False)
    assert dl.main() == 0
