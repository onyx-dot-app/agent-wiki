"""Installer endpoint — script + binary + .app zip."""

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


def test_installer_app_404_when_zip_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/app")
    assert res.status_code == 503
    assert "AgentWikiLauncher.zip missing" in res.json()["error"]


def test_installer_app_streams_zip_when_present(client, tmp_path, monkeypatch):
    payload = b"PK\x03\x04test-zip-content"
    (tmp_path / "AgentWikiLauncher.zip").write_bytes(payload)
    monkeypatch.setattr("app.api.installer._BINARIES_DIR", tmp_path)
    res = client.get("/api/installer/app")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "AgentWikiLauncher.zip" in res.headers["content-disposition"]
    assert res.content == payload


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
