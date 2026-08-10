"""Aspect-state generation (app/ingest/aspect_state.py) — the mechanical
single-page path, the unified fan-out path with its conflict verdict, the
timestamp staleness skip, and dangling-link tolerance.

DB-backed via ``tmp_db``. The LLM is mocked at the house seam
(``json_completion.complete_json`` — itself a wrapper over
``app.llm.client.complete``); the mechanical path asserts the seam is never
touched at all.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.db import aspect_states, need_map, page_needs
from app.ingest import aspect_state


def _seed_page(path: str, needs: list[dict[str, Any]], *, body: str = "") -> str:
    return page_needs.store(path, body=body or ("body of " + path), needs=needs, model="test-model")


def _need(name: str, content: str) -> dict[str, Any]:
    return {"need_name": name, "current_content": content}


def _seed_map(topics: list[dict[str, Any]]) -> int:
    map_id = need_map.record(
        {
            "corpus_fingerprint": "fp",
            "entity_type_taxonomy_id": None,
            "provenance": {},
            "stats": {},
            "topics": topics,
        }
    )
    return map_id


@pytest.fixture
def corpus(tmp_db):
    a = _seed_page("eng/status.md", [_need("delivery status", "slice 3 merged; watch until Friday")])
    b = _seed_page("notes/todo.md", [_need("delivery progress", "slice 3 still in review")])
    c = _seed_page("eng/design.md", [_need("architecture", "attach-by-transplant; sessions stay the working copy")])
    map_id = _seed_map(
        [
            {
                "name": "Rollout",
                "description": "The rollout of the thing.",
                "aspects": [
                    {
                        "key": "0:delivery",
                        "name": "delivery status",
                        "description": "Where delivery stands.",
                        "pages": [
                            {"doc_id": a, "need_name": "delivery status"},
                            {"doc_id": b, "need_name": "delivery progress"},
                        ],
                    },
                    {
                        "key": "0:architecture",
                        "name": "architecture",
                        "description": "How it is built.",
                        "pages": [{"doc_id": c, "need_name": "architecture"}],
                    },
                ],
            }
        ]
    )
    return map_id


def _aspect_ids(map_id: int) -> dict[str, int]:
    record = need_map.get(map_id)
    assert record is not None
    return {a.name: a.aspect_id for t in record.topics for a in t.aspects}


def test_single_page_aspect_is_mechanical_and_free(corpus, monkeypatch):
    monkeypatch.setattr(
        aspect_state.json_completion,
        "complete_json",
        lambda *a, **k: {"state": "unified", "conflict": False, "conflict_note": ""},
    )
    stats = aspect_state.run_generation(corpus)
    assert stats is not None
    ids = _aspect_ids(corpus)
    stored = aspect_states.get(ids["architecture"])
    assert stored is not None
    assert stored.state == "attach-by-transplant; sessions stay the working copy"
    assert stored.model == ""  # no model produced it
    assert stored.conflict is False


def test_fan_out_aspect_unifies_and_flags_conflict(corpus, monkeypatch):
    calls: list[str] = []

    def fake(system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(user)
        return {
            "state": "slice 3: merged per the engineering page, in review per the TODO",
            "conflict": True,
            "conflict_note": "eng/status.md says merged; notes/todo.md says in review",
        }

    monkeypatch.setattr(aspect_state.json_completion, "complete_json", fake)
    stats = aspect_state.run_generation(corpus)
    assert stats is not None
    assert stats["conflicts"] == 1
    assert len(calls) == 1  # exactly the one fan-out aspect
    assert "eng/status.md" in calls[0] and "notes/todo.md" in calls[0]
    ids = _aspect_ids(corpus)
    stored = aspect_states.get(ids["delivery status"])
    assert stored is not None
    assert stored.conflict is True
    assert "in review" in stored.conflict_note


def test_fresh_states_are_skipped(corpus, monkeypatch):
    monkeypatch.setattr(
        aspect_state.json_completion,
        "complete_json",
        lambda *a, **k: {"state": "unified", "conflict": False, "conflict_note": ""},
    )
    first = aspect_state.run_generation(corpus)
    assert first is not None and first["fresh"] == 0

    def boom(*_a, **_k):
        raise AssertionError("a fresh corpus must not reach the LLM")

    monkeypatch.setattr(aspect_state.json_completion, "complete_json", boom)
    second = aspect_state.run_generation(corpus)
    assert second is not None
    assert second["fresh"] == second["aspects"]
    assert second["mechanical"] == 0 and second["unified"] == 0


def test_failed_unification_is_counted_not_stored(corpus, monkeypatch):
    monkeypatch.setattr(aspect_state.json_completion, "complete_json", lambda *a, **k: None)
    stats = aspect_state.run_generation(corpus)
    assert stats is not None
    assert stats["failed"] == 1
    ids = _aspect_ids(corpus)
    assert aspect_states.get(ids["delivery status"]) is None  # nothing guessed
    assert aspect_states.get(ids["architecture"]) is not None  # mechanical path unaffected


def test_dangling_links_resolve_to_what_remains(corpus, monkeypatch):
    # Re-extract one member page so its need name no longer matches the map's link.
    ids = _aspect_ids(corpus)
    record = need_map.get(corpus)
    assert record is not None
    b_doc = next(
        n.doc_id
        for t in record.topics
        for a in t.aspects
        if a.name == "delivery status"
        for n in a.needs
        if n.need_name == "delivery progress"
    )
    b_row = page_needs.get_by_doc_id(b_doc)
    assert b_row is not None
    page_needs.store(
        b_row.path, body="changed body", needs=[_need("renamed need", "different now")], model="test-model"
    )
    monkeypatch.setattr(
        aspect_state.json_completion,
        "complete_json",
        lambda *a, **k: {"state": "should not be called", "conflict": False, "conflict_note": ""},
    )
    stats = aspect_state.run_generation(corpus)
    assert stats is not None
    # The fan-out aspect degraded to one resolvable member -> mechanical path.
    stored = aspect_states.get(ids["delivery status"])
    assert stored is not None
    assert stored.state == "slice 3 merged; watch until Friday"
    assert stored.model == ""
