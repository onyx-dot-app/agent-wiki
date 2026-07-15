"""Tests for the two-tower HTTP scorer client.

The network is mocked (patching ``Session.post``) — we assert the wire contract
(URL, payload, parse) and that failures propagate as exceptions so
``TwoTowerFilter`` fails open.
"""
from __future__ import annotations

import pytest
import requests

from app.ingest.relevance import TwoTowerFilter, TwoTowerScorer, two_tower_scorer
from app.ingest.types import CandidatePage, IngestionDocument


class _FakeResponse:
    def __init__(self, payload: dict | None, *, ok: bool = True) -> None:
        self._payload = payload
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise requests.HTTPError("model server 500")

    def json(self) -> dict:
        assert self._payload is not None
        return self._payload


def test_score_batch_posts_contract_and_parses(monkeypatch):
    captured: dict = {}

    def fake_post(self, url, json=None, timeout=None):
        captured.update(url=url, json=json, timeout=timeout)
        return _FakeResponse({"probs": [0.9, 0.1]})

    monkeypatch.setattr(two_tower_scorer.requests.Session, "post", fake_post)

    scorer = TwoTowerScorer("http://model-server:9100/")
    probs = scorer.score_batch([1.0, 0.0], [[0.1, 0.2], [0.3, 0.4]])

    assert probs == [0.9, 0.1]
    assert captured["url"] == "http://model-server:9100/score"  # trailing slash trimmed
    assert captured["json"] == {
        "doc_vec": [1.0, 0.0],
        "page_vecs": [[0.1, 0.2], [0.3, 0.4]],
    }
    assert captured["timeout"] == two_tower_scorer.DEFAULT_TIMEOUT_SECONDS


def test_score_batch_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        two_tower_scorer.requests.Session,
        "post",
        lambda self, url, json=None, timeout=None: _FakeResponse(None, ok=False),
    )
    with pytest.raises(requests.HTTPError):
        TwoTowerScorer("http://model-server:9100").score_batch([1.0], [[1.0]])


def test_filter_fails_open_when_model_server_errors(monkeypatch):
    # End-to-end: TwoTowerScorer raises → TwoTowerFilter keeps all candidates.
    def boom(self, url, json=None, timeout=None):
        raise requests.ConnectionError("model server unreachable")

    monkeypatch.setattr(two_tower_scorer.requests.Session, "post", boom)

    f = TwoTowerFilter(TwoTowerScorer("http://model-server:9100"), threshold=0.99)
    doc = IngestionDocument(content="d", embedding=[1.0, 0.0])
    pages = [
        CandidatePage(path="a.md", body="b", embedding=[0.1, 0.2]),
        CandidatePage(path="b.md", body="b", embedding=[0.3, 0.4]),
    ]
    assert f.keep_relevant(doc, pages) == pages
