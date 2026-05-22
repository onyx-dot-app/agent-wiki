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
# /installer/linux — default = AppImage, fallback = tarball
# ---------------------------------------------------------------------------


def test_installer_linux_default_is_deb(client, tmp_path, monkeypatch):
    payload = b"!<arch>\ntest-deb"
    (tmp_path / "agentwiki-launcher_0.1.0_amd64.deb").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/vnd.debian.binary-package"
    assert "agentwiki-launcher_0.1.0_amd64.deb" in res.headers["content-disposition"]
    assert res.content == payload


def test_installer_linux_deb_picks_newest_version(client, tmp_path, monkeypatch):
    """When multiple .deb files exist (after a version bump), serve the highest version."""
    (tmp_path / "agentwiki-launcher_0.9.0_amd64.deb").write_bytes(b"old")
    (tmp_path / "agentwiki-launcher_0.10.0_amd64.deb").write_bytes(b"new")
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=deb")
    assert res.status_code == 200
    assert "0.10.0" in res.headers["content-disposition"]
    assert res.content == b"new"


def test_installer_linux_deb_503_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=deb")
    assert res.status_code == 503
    assert ".deb missing" in res.json()["error"]


def test_installer_linux_rpm_streams_when_present(client, tmp_path, monkeypatch):
    payload = b"\xed\xab\xee\xdbtest-rpm"
    (tmp_path / "agentwiki-launcher-0.1.0-1.x86_64.rpm").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=rpm")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/x-rpm"
    assert res.content == payload


def test_installer_linux_rpm_picks_newest_version(client, tmp_path, monkeypatch):
    (tmp_path / "agentwiki-launcher-0.9.0-1.el9.x86_64.rpm").write_bytes(b"old")
    (tmp_path / "agentwiki-launcher-0.10.0-1.fc40.x86_64.rpm").write_bytes(b"new")
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=rpm")
    assert res.status_code == 200
    assert "0.10.0" in res.headers["content-disposition"]
    assert res.content == b"new"


def test_installer_linux_rpm_503_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=rpm")
    assert res.status_code == 503
    assert ".rpm missing" in res.json()["error"]


def test_installer_linux_appimage(client, tmp_path, monkeypatch):
    payload = b"\x7fELF-test-appimage"
    (tmp_path / "AgentWikiLauncher-x86_64.AppImage").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=appimage")
    assert res.status_code == 200
    assert "AgentWikiLauncher-x86_64.AppImage" in res.headers["content-disposition"]
    assert res.content == payload


def test_installer_linux_appimage_503_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=appimage")
    assert res.status_code == 503
    assert "AppImage missing" in res.json()["error"]


def test_installer_linux_appimage_404_for_non_amd64(client):
    res = client.get("/api/installer/linux?format=appimage&arch=arm64")
    assert res.status_code == 404
    assert "appimage is amd64-only" in res.json()["error"]


def test_installer_linux_tarball_amd64(client, tmp_path, monkeypatch):
    payload = b"\x1f\x8b\x08test-linux-tar"
    (tmp_path / "agentwiki-launcher-linux-amd64.tar.gz").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=tar.gz&arch=amd64")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/gzip"
    assert "linux-amd64.tar.gz" in res.headers["content-disposition"]
    assert res.content == payload


def test_installer_linux_tarball_arm64(client, tmp_path, monkeypatch):
    payload = b"\x1f\x8b\x08test-linux-arm64"
    (tmp_path / "agentwiki-launcher-linux-arm64.tar.gz").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=tar.gz&arch=arm64")
    assert res.status_code == 200
    assert res.content == payload


def test_installer_linux_tarball_503_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/linux?format=tar.gz&arch=amd64")
    assert res.status_code == 503
    assert "linux-amd64.tar.gz missing" in res.json()["error"]


def test_installer_linux_404_unknown_arch(client):
    res = client.get("/api/installer/linux?format=tar.gz&arch=mips")
    assert res.status_code == 404
    assert "unsupported linux arch" in res.json()["error"]


def test_installer_linux_404_unknown_format(client):
    res = client.get("/api/installer/linux?format=snap")
    assert res.status_code == 404
    assert "unsupported linux format" in res.json()["error"]
    # Error message lists all supported formats so the operator knows
    # what's valid without grepping the source.
    for fmt in ("deb", "rpm", "appimage", "tar.gz"):
        assert fmt in res.json()["error"]


# ---------------------------------------------------------------------------
# /installer/windows
# ---------------------------------------------------------------------------


def test_installer_windows_503_when_exe_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/windows")
    assert res.status_code == 503
    assert "windows-amd64.exe missing" in res.json()["error"]


def test_installer_windows_streams_exe_when_present(client, tmp_path, monkeypatch):
    payload = b"MZ\x90\x00test-windows-exe"
    (tmp_path / "agentwiki-launcher-windows-amd64.exe").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/windows")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/octet-stream"
    assert "windows-amd64.exe" in res.headers["content-disposition"]
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
