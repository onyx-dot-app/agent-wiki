"""Tests for the web_search and open_urls tools.

We patch the seams (``app.web.search`` / ``app.web.fetch``) — never the
underlying Serper/Firecrawl SDK clients.
"""
from __future__ import annotations

from datetime import datetime, timezone


from app import web as web_pkg
from app.web.models import WebContent, WebSearchResult


# --------------------------------------------------------------------------- #
# web_search                                                                  #
# --------------------------------------------------------------------------- #


def test_web_search_validation_requires_query():
    from app.llm.agents.tools.web_search import handle

    assert handle({})["error"] == "query is required"
    assert handle({"query": "   "})["error"] == "query is required"


def test_web_search_returns_normalized_rows(monkeypatch):
    from app.llm.agents.tools import web_search as ws

    captured = {}

    def fake_search(query, *, num_results):
        captured["query"] = query
        captured["num_results"] = num_results
        return [
            WebSearchResult(
                title="Flask 3.0",
                link="https://flask.palletsprojects.com/",
                snippet="Flask 3.0 release notes…",
                published_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(ws.web, "search", fake_search)

    out = ws.handle({"query": "  flask 3.0  ", "num_results": 5})
    assert "error" not in out
    assert captured == {"query": "flask 3.0", "num_results": 5}
    assert out["results"] == [
        {
            "title": "Flask 3.0",
            "link": "https://flask.palletsprojects.com/",
            "snippet": "Flask 3.0 release notes…",
            "published_date": "2024-01-01T00:00:00+00:00",
        }
    ]


def test_web_search_clamps_num_results_to_max(monkeypatch):
    from app.llm.agents.tools import web_search as ws

    seen = {}

    def fake_search(query, *, num_results):
        seen["num_results"] = num_results
        return []

    monkeypatch.setattr(ws.web, "search", fake_search)
    ws.handle({"query": "x", "num_results": 9999})
    assert seen["num_results"] == ws.MAX_NUM


def test_web_search_surfaces_provider_not_configured(monkeypatch):
    from app.llm.agents.tools import web_search as ws

    def boom(*a, **kw):
        raise web_pkg.WebProviderNotConfigured("Serper API key is not configured")

    monkeypatch.setattr(ws.web, "search", boom)
    out = ws.handle({"query": "x"})
    assert "error" in out
    assert "Serper" in out["error"]


def test_web_search_surfaces_unexpected_errors(monkeypatch):
    from app.llm.agents.tools import web_search as ws

    monkeypatch.setattr(
        ws.web, "search", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = ws.handle({"query": "x"})
    assert "error" in out
    assert "boom" in out["error"]


# --------------------------------------------------------------------------- #
# open_urls                                                                   #
# --------------------------------------------------------------------------- #


def test_open_urls_validation_requires_url_list():
    from app.llm.agents.tools.open_urls import handle

    assert "urls is required" in handle({})["error"]
    assert "urls is required" in handle({"urls": []})["error"]
    assert "non-empty string" in handle({"urls": [""]})["error"]
    assert "http" in handle({"urls": ["not-a-url"]})["error"]
    assert "http" in handle({"urls": ["ftp://x.example.com"]})["error"]


def test_open_urls_rejects_too_many():
    from app.llm.agents.tools import open_urls as ou

    out = ou.handle({"urls": [f"https://example.com/{i}" for i in range(ou.MAX_URLS + 1)]})
    assert "too many urls" in out["error"]


def test_open_urls_returns_normalized_content(monkeypatch):
    from app.llm.agents.tools import open_urls as ou

    seen = {}

    def fake_fetch(urls):
        seen["urls"] = list(urls)
        return [
            WebContent(
                title="Hello",
                link="https://example.com/",
                full_content="# Hello\n\nbody",
                published_date=None,
                scrape_successful=True,
            ),
            WebContent(
                title="World",
                link="https://example.org/",
                full_content="body 2",
                published_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                scrape_successful=True,
            ),
        ]

    monkeypatch.setattr(ou.web, "fetch", fake_fetch)
    out = ou.handle(
        {"urls": ["  https://example.com/  ", "https://example.org/"]}
    )
    assert seen["urls"] == ["https://example.com/", "https://example.org/"]
    assert "error" not in out
    assert len(out["results"]) == 2
    first = out["results"][0]
    assert first["title"] == "Hello"
    assert first["full_content"].startswith("# Hello")
    assert first["scrape_successful"] is True
    assert first["published_date"] is None
    assert out["results"][1]["published_date"] == "2024-01-01T00:00:00+00:00"


def test_open_urls_handles_provider_not_configured(monkeypatch):
    from app.llm.agents.tools import open_urls as ou

    def boom(urls):
        raise web_pkg.WebProviderNotConfigured("Firecrawl API key is not configured")

    monkeypatch.setattr(ou.web, "fetch", boom)
    out = ou.handle({"urls": ["https://example.com/"]})
    assert "error" in out
    assert "Firecrawl" in out["error"]


def test_open_urls_handles_empty_response(monkeypatch):
    from app.llm.agents.tools import open_urls as ou

    monkeypatch.setattr(ou.web, "fetch", lambda urls: [])
    out = ou.handle({"urls": ["https://example.com/"]})
    assert out["results"] == []
