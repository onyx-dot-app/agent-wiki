"""Installer endpoint — per-platform downloads + legacy mac script + binary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db
from app.main import create_app


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


def _installers_dir() -> Path:
    from app.api import installer

    return installer._BINARIES_DIR


# ---------------------------------------------------------------------------
# /installer/mac + /installer/app (back-compat alias)
# ---------------------------------------------------------------------------


def test_installer_mac_503_when_zip_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/mac")
    assert res.status_code == 503
    assert "AgentWikiLauncher.zip missing" in res.json()["error"]


def test_installer_mac_streams_zip_when_present(client, tmp_path, monkeypatch):
    payload = b"PK\x03\x04test-mac-zip"
    (tmp_path / "AgentWikiLauncher.zip").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/mac")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "AgentWikiLauncher.zip" in res.headers["content-disposition"]
    assert res.content == payload


def test_installer_app_is_alias_for_mac(client, tmp_path, monkeypatch):
    """Old FE builds hit /installer/app; must still resolve to the mac zip."""
    payload = b"PK\x03\x04test-app-alias"
    (tmp_path / "AgentWikiLauncher.zip").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/app")
    assert res.status_code == 200
    assert res.content == payload


# ---------------------------------------------------------------------------
# /installer/linux
# ---------------------------------------------------------------------------


def test_installer_linux_503_when_tarball_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?arch=amd64")
    assert res.status_code == 503
    assert "linux-amd64.tar.gz missing" in res.json()["error"]


def test_installer_linux_streams_amd64_when_present(client, tmp_path, monkeypatch):
    payload = b"\x1f\x8b\x08test-linux-tar"
    (tmp_path / "agentwiki-launcher-linux-amd64.tar.gz").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?arch=amd64")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/gzip"
    assert "linux-amd64.tar.gz" in res.headers["content-disposition"]
    assert res.content == payload


def test_installer_linux_streams_arm64_when_present(client, tmp_path, monkeypatch):
    payload = b"\x1f\x8b\x08test-linux-arm64"
    (tmp_path / "agentwiki-launcher-linux-arm64.tar.gz").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?arch=arm64")
    assert res.status_code == 200
    assert res.content == payload


def test_installer_linux_default_arch_is_amd64(client, tmp_path, monkeypatch):
    payload = b"\x1f\x8b\x08test-default-amd64"
    (tmp_path / "agentwiki-launcher-linux-amd64.tar.gz").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux")
    assert res.status_code == 200
    assert res.content == payload


def test_installer_linux_404_unknown_arch(client):
    res = client.get("/api/installer/linux?arch=mips")
    assert res.status_code == 404
    assert "unsupported linux arch" in res.json()["error"]


# ---------------------------------------------------------------------------
# /installer/windows
# ---------------------------------------------------------------------------


def test_installer_windows_503_when_zip_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/windows")
    assert res.status_code == 503
    assert "windows-amd64.zip missing" in res.json()["error"]


def test_installer_windows_streams_zip_when_present(client, tmp_path, monkeypatch):
    payload = b"PK\x03\x04test-windows-zip"
    (tmp_path / "agentwiki-launcher-windows-amd64.zip").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/windows")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "windows-amd64.zip" in res.headers["content-disposition"]
    assert res.content == payload


# ---------------------------------------------------------------------------
# Legacy /installer/binary + /installer/script
# ---------------------------------------------------------------------------


def test_installer_binary_404_unknown_arch(client):
    res = client.get("/api/installer/binary?arch=linux-arm64")
    assert res.status_code == 404


def test_installer_script_bakes_request_base_url(client):
    res = client.get("/api/installer/script")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/x-shellscript")
    body = res.text
    assert 'WIKI_URL="http://' in body, body
    assert "set-endpoint" in body
    assert "install" in body
