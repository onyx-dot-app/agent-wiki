"""GET /api/wiki/source-spans, live content-to-source spans for the FE
highlighter. Real wiki repo so the read-path remap has a repo to read. Pages
committed via wiki_git are unmanaged, so the ACL resolver grants read.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.wiki import WriteProvenance
from app.wiki import git as wiki_git
from app.wiki import provenance
from tests._auth import login_fastapi
from tests._seed import seed_user

_PATH = "guides/setup.md"
_BODY = "Alpha beta gamma.\n"


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())


def _seed_span(source: WriteProvenance) -> None:
    sha = wiki_git.commit_file(_PATH, _BODY, "seed", author=None)
    pid = provenance.record(
        commit_sha=sha,
        doc_path=_PATH,
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=source,
    )
    assert pid is not None
    provenance.capture_source_ranges(
        provenance_id=pid, doc_path=_PATH, anchor_sha=sha, old_body="", new_body=_BODY
    )


def test_source_spans_returns_spans_with_source(client):
    login_fastapi(client, seed_user("u1", email="u1@x.com", name="U"))
    _seed_span(WriteProvenance(source_document_id="d1", source_title="Src One"))
    resp = client.get("/api/wiki/source-spans", params={"path": _PATH})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    span = data[0]
    assert (span["start_offset"], span["end_offset"]) == (0, len(_BODY))
    assert span["source_document_id"] == "d1"
    assert span["source_title"] == "Src One"


def test_source_spans_empty_for_page_without_ingest(client):
    login_fastapi(client, seed_user("u2", email="u2@x.com", name="U2"))
    wiki_git.commit_file(_PATH, _BODY, "seed", author=None)
    resp = client.get("/api/wiki/source-spans", params={"path": _PATH})
    assert resp.status_code == 200
    assert resp.json() == []


def test_source_spans_requires_path(client):
    login_fastapi(client, seed_user("u3", email="u3@x.com", name="U3"))
    assert client.get("/api/wiki/source-spans").status_code == 400
