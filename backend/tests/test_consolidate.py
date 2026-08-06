"""Turning clusters into topics and aspects.

The LLM call is not pinned here. What is pinned is everything that decides whether its output can
be trusted into the map: that a partial or malformed response loses a need rather than a cluster,
that a need cannot be written under two aspects, that a failed derivation is not recorded as a map
saying the corpus shares nothing, and that the fan-out counted in the stats is the fan-out stored.
"""

from __future__ import annotations

from typing import Any

from app.ingest import consolidate, topics


def _need(name: str, **over: Any) -> dict[str, Any]:
    return {
        "need_name": name,
        "need_kind": "reference",
        "description": "",
        "detail_level": "",
        "update_instruction": "",
        "current_content": "",
        "entities": [],
        "focus": "specific",
    } | over


def _ref(doc_id: str, name: str, **over: Any) -> topics.NeedRef:
    return topics.NeedRef(doc_id=doc_id, path=f"{doc_id}.md", need=_need(name, **over))


def _cluster(*refs: topics.NeedRef) -> topics.Cluster:
    return topics.Cluster(members=list(refs))


def _stub(monkeypatch, payload: dict[str, Any] | None) -> None:
    monkeypatch.setattr(consolidate.json_completion, "complete_json", lambda *a, **k: payload)


def _topic(name: str, *aspects: dict[str, Any]) -> dict[str, Any]:
    return {"topic_name": name, "topic_description": f"about {name}", "aspects": list(aspects)}


def _aspect(name: str, indices: list[int]) -> dict[str, Any]:
    return {"aspect_name": name, "aspect_description": f"tracks {name}", "member_indices": indices}


class TestNameCluster:
    def test_splits_a_cluster_into_topics_and_their_aspects(self, monkeypatch) -> None:
        """The two levels from one call: the embedding merged two subjects, and within one of
        them two needs turn out to track the same facet from different pages."""
        _stub(
            monkeypatch,
            {
                "topics": [
                    _topic("Wiki Auto Management", _aspect("implementation status", [1, 2])),
                    _topic("Craft Integration", _aspect("rollout status", [3])),
                ]
            },
        )
        cluster = _cluster(
            _ref("a", "impl status and deferred work"),
            _ref("b", "wiki auto implementation status"),
            _ref("c", "craft rollout"),
        )

        drafts = consolidate.name_cluster(cluster)

        assert [d.name for d in drafts] == ["Wiki Auto Management", "Craft Integration"]
        shared = drafts[0].aspects[0]
        assert shared.name == "implementation status"
        assert sorted(m.doc_id for m in shared.members) == ["a", "b"]

    def test_a_need_claimed_twice_lands_in_one_aspect(self, monkeypatch) -> None:
        """A need written under two aspects would be counted twice in the fan-out — the number
        the whole layer is justified by. First claim wins; the second aspect keeps the rest."""
        _stub(
            monkeypatch,
            {"topics": [_topic("T", _aspect("first", [1, 2]), _aspect("second", [2, 3]))]},
        )
        cluster = _cluster(_ref("a", "one"), _ref("b", "two"), _ref("c", "three"))

        drafts = consolidate.name_cluster(cluster)

        first, second = drafts[0].aspects
        assert [m.doc_id for m in first.members] == ["a", "b"]
        assert [m.doc_id for m in second.members] == ["c"]

    def test_an_aspect_left_with_nothing_is_dropped_not_emptied(self, monkeypatch) -> None:
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("first", [1]), _aspect("dupe", [1]))]})

        drafts = consolidate.name_cluster(_cluster(_ref("a", "one")))

        assert [a.name for a in drafts[0].aspects] == ["first"]

    def test_an_out_of_range_index_costs_that_need_not_the_cluster(self, monkeypatch) -> None:
        """A hallucinated index is one need's placement, not a reason to discard structure the
        call has already been paid for."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1, 99]))]})

        drafts = consolidate.name_cluster(_cluster(_ref("a", "one"), _ref("b", "two")))

        assert [m.doc_id for m in drafts[0].aspects[0].members] == ["a"]

    def test_unplaced_needs_are_absent_rather_than_guessed(self, monkeypatch, caplog) -> None:
        """The prompt demands a partition. When it isn't one, the missing needs stay out of the
        map — a guessed placement is a confident wrong row — and the gap is logged."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1]))]})

        with caplog.at_level("WARNING"):
            drafts = consolidate.name_cluster(_cluster(_ref("a", "one"), _ref("b", "two")))

        placed = [m.doc_id for t in drafts for a in t.aspects for m in a.members]
        assert placed == ["a"]
        assert "left unplaced" in caplog.text

    def test_a_failed_call_yields_no_topic(self, monkeypatch) -> None:
        """Not a fallback topic named after the first member: a cluster the model could not
        structure is one we know nothing about."""
        _stub(monkeypatch, None)

        assert consolidate.name_cluster(_cluster(_ref("a", "one"))) == []

    def test_a_topic_without_a_usable_aspect_is_dropped(self, monkeypatch) -> None:
        _stub(monkeypatch, {"topics": [{"topic_name": "T", "aspects": [{"aspect_name": ""}]}]})

        assert consolidate.name_cluster(_cluster(_ref("a", "one"))) == []

    def test_junk_entries_do_not_abort_the_response(self, monkeypatch) -> None:
        _stub(
            monkeypatch,
            {"topics": ["nonsense", {"topic_name": "T", "aspects": ["junk", _aspect("ok", [1])]}]},
        )

        drafts = consolidate.name_cluster(_cluster(_ref("a", "one")))

        assert [a.name for a in drafts[0].aspects] == ["ok"]


class TestListing:
    def test_names_the_page_so_fan_out_is_visible(self) -> None:
        """Two pages wording a need identically IS the fan-out. Without the page the model cannot
        tell that from one page restating itself."""
        listing = consolidate._listing([_ref("a", "deal status"), _ref("b", "deal status")])

        assert "page=a" in listing and "page=b" in listing
        assert listing.count("deal status") == 2

    def test_excludes_the_state(self) -> None:
        """``current_content`` is the largest field and the most volatile; what a need TRACKS is
        what decides where it belongs."""
        listing = consolidate._listing(
            [_ref("a", "deal status", current_content="Acme: negotiating. Globex: won.")]
        )

        assert "Acme" not in listing and "negotiating" not in listing


class TestConsolidate:
    def test_a_failing_cluster_does_not_take_the_others(self, monkeypatch) -> None:
        calls = {"n": 0}

        def flaky(*a: Any, **k: Any) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return {"topics": [_topic("T", _aspect("status", [1]))]}

        monkeypatch.setattr(consolidate.json_completion, "complete_json", flaky)

        drafts = consolidate.consolidate(
            [_cluster(_ref("a", "one")), _cluster(_ref("b", "two"))], workers=1
        )

        assert [d.name for d in drafts] == ["T"]

    def test_no_clusters_is_no_topics(self) -> None:
        assert consolidate.consolidate([]) == []


class TestRunConsolidation:
    """The outcomes that must NOT be recorded. Each would deactivate a good map and read as
    "the corpus shares nothing", which is indistinguishable from a real finding."""

    @staticmethod
    def _refuse(monkeypatch) -> list[Any]:
        recorded: list[Any] = []
        monkeypatch.setattr(consolidate.need_map, "record", lambda *a, **k: recorded.append(a))
        return recorded

    def test_no_needs_records_nothing(self, tmp_db, monkeypatch) -> None:
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(consolidate.topics, "load_needs", list)

        assert consolidate.run_consolidation() is None
        assert recorded == []

    def test_unavailable_embeddings_record_nothing(self, tmp_db, monkeypatch) -> None:
        """``cluster_needs`` returns None when it cannot embed. Recording then would store a map
        derived from no signal at all."""
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(consolidate.topics, "load_needs", lambda: [_ref("a", "one")])
        monkeypatch.setattr(consolidate.topics, "cluster_needs", lambda refs: None)

        assert consolidate.run_consolidation() is None
        assert recorded == []

    def test_naming_that_produced_nothing_records_nothing(self, tmp_db, monkeypatch) -> None:
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(consolidate.topics, "load_needs", lambda: [_ref("a", "one")])
        monkeypatch.setattr(
            consolidate.topics, "cluster_needs", lambda refs: [_cluster(*refs)]
        )
        _stub(monkeypatch, None)

        assert consolidate.run_consolidation() is None
        assert recorded == []


class TestArtifact:
    def test_two_topics_may_each_have_a_like_named_aspect(self) -> None:
        """Aspect keys are scoped to the topic. An aspect belongs to one topic, so two subjects
        each tracking "implementation status" are two facets — not one shared row."""
        drafts = [
            consolidate.TopicDraft("A", "", [consolidate.AspectDraft("status", "", [_ref("a", "x")])]),
            consolidate.TopicDraft("B", "", [consolidate.AspectDraft("status", "", [_ref("b", "y")])]),
        ]

        artifact = consolidate._artifact(
            drafts, fingerprint="f", entity_type_taxonomy_id=None, model="m", stats={}
        )

        keys = [a["key"] for t in artifact["topics"] for a in t["aspects"]]
        assert len(set(keys)) == 2

    def test_carries_the_need_name_the_map_points_back_with(self) -> None:
        """``aspect_pages`` is keyed (aspect, doc_id, need_name) — the artifact must supply the
        name, or every link would collide on "" and all but one would be dropped."""
        drafts = [
            consolidate.TopicDraft(
                "A", "", [consolidate.AspectDraft("status", "", [_ref("a", "shipped features"), _ref("a", "deferred work")])]
            )
        ]

        artifact = consolidate._artifact(
            drafts, fingerprint="f", entity_type_taxonomy_id=None, model="m", stats={}
        )

        pages = artifact["topics"][0]["aspects"][0]["pages"]
        assert sorted(p["need_name"] for p in pages) == ["deferred work", "shipped features"]


class TestEndToEnd:
    def test_a_derivation_lands_as_a_readable_map(self, tmp_repo, monkeypatch) -> None:
        """The join the unit tests cannot make: the artifact this module builds is the artifact
        ``need_map.record`` accepts. A field renamed on either side passes every test above and
        fails here.
        """
        from app.db import need_map, page_needs
        from app.wiki import git as wiki_git

        for path, names in (
            ("engineering.md", ["implementation status and deferred work"]),
            ("bo-todo.md", ["wiki auto implementation status"]),
            ("fonts.md", ["font choices"]),
        ):
            wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
            page_needs.store(path, body="body", needs=[_need(n) for n in names])

        refs = topics.load_needs()
        shared = [r for r in refs if "implementation" in r.need["need_name"]]
        alone = [r for r in refs if "font" in r.need["need_name"]]
        monkeypatch.setattr(
            consolidate.topics, "cluster_needs", lambda _: [_cluster(*shared), _cluster(*alone)]
        )

        payloads = iter(
            [
                {"topics": [_topic("Wiki Auto Management", _aspect("implementation status", [1, 2]))]},
                {"topics": [_topic("Typography", _aspect("font choices", [1]))]},
            ]
        )
        monkeypatch.setattr(
            consolidate.json_completion, "complete_json", lambda *a, **k: next(payloads)
        )

        map_id = consolidate.run_consolidation(workers=1)

        assert map_id is not None
        loaded = need_map.active()
        assert loaded is not None
        assert loaded.need_map_id == map_id
        assert sorted(t.name for t in loaded.topics) == ["Typography", "Wiki Auto Management"]

        by_name = {t.name: t for t in loaded.topics}
        spanning = by_name["Wiki Auto Management"].aspects[0]
        assert spanning.name == "implementation status"
        assert spanning.spans_pages
        assert len(spanning.doc_ids) == 2
        assert not by_name["Typography"].aspects[0].spans_pages

        # the stats a run is judged by must describe the rows it actually wrote
        assert loaded.stats["n_topics"] == 2
        assert loaded.stats["n_aspects"] == 2
        assert loaded.stats["n_aspects_spanning_pages"] == 1
        assert loaded.stats["widest_aspect_pages"] == 2
        # ``model`` is "" here because no LLM is configured in tests; what must be recorded is
        # the knob the grouping actually depended on.
        assert loaded.provenance["cluster_similarity"] == topics.CLUSTER_SIMILARITY
        assert "model" in loaded.provenance
        assert loaded.corpus_fingerprint

    def test_the_reverse_lookup_finds_a_page_after_a_derivation(self, tmp_repo, monkeypatch) -> None:
        """The query a reconciler runs per incoming document. It is the only reason the map is
        tables rather than a blob, so it is worth proving end to end rather than from fixtures."""
        from app.db import need_map, page_needs
        from app.wiki import doc_ids, git as wiki_git

        for path in ("a.md", "b.md"):
            wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
            page_needs.store(path, body="body", needs=[_need("deal status")])

        monkeypatch.setattr(
            consolidate.topics, "cluster_needs", lambda refs: [_cluster(*refs)]
        )
        monkeypatch.setattr(
            consolidate.json_completion,
            "complete_json",
            lambda *a, **k: {"topics": [_topic("Customers", _aspect("deal status", [1, 2]))]},
        )

        assert consolidate.run_consolidation(workers=1) is not None

        found = need_map.aspects_for_page(doc_ids.get_or_mint("a.md"))
        assert [a.name for a in found] == ["deal status"]
        # ...and it reports the OTHER page too — that is the fan-out a reconciler acts on
        assert len(found[0].doc_ids) == 2
