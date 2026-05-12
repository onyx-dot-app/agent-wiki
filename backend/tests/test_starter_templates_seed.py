"""Tests for the bundled starter document-template seed.

Covers:

* fresh DB → ``seed_starter_templates_if_empty`` inserts every bundled
  template, and a second call is a no-op (one-shot contract).
* every bundled file parses (valid frontmatter, non-empty name + body).
* the CLI-style ``write_starter_templates(skip_existing=True)`` path
  layers in only the missing rows when the table is already populated.
* a deleted starter template does not come back on the next seed call.
"""
from __future__ import annotations

import pytest


def test_iter_starter_templates_parses_every_bundled_file():
    from app.wiki.templates import iter_starter_templates

    rows = iter_starter_templates()
    if not rows:
        pytest.skip("no bundled starter templates available in this environment")

    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)), f"duplicate template names: {names}"
    for row in rows:
        assert row["name"], f"empty name in row: {row}"
        assert row["body"].strip(), f"empty body for {row['name']!r}"


def test_seed_starter_templates_if_empty_inserts_all(tmp_db):
    from app.wiki.templates import (
        count,
        iter_starter_templates,
        list_all,
        seed_starter_templates_if_empty,
    )

    expected = {r["name"] for r in iter_starter_templates()}
    if not expected:
        pytest.skip("no bundled starter templates available in this environment")
    assert count() == 0

    seeded = seed_starter_templates_if_empty()

    assert seeded is True
    assert {row["name"] for row in list_all()} == expected


def test_seed_is_idempotent_when_table_populated(tmp_db):
    """Once any row exists, the seed must not run — even if some bundled
    templates are missing. This is the contract that lets a user delete
    a starter and have it stay deleted across reboots."""
    from app.wiki import templates as templates_repo

    if not templates_repo.iter_starter_templates():
        pytest.skip("no bundled starter templates available in this environment")

    assert templates_repo.seed_starter_templates_if_empty() is True
    rows_before = templates_repo.list_all()

    # Delete one starter — it must stay deleted.
    victim = rows_before[0]
    assert templates_repo.delete(victim["id"]) is True

    re_seeded = templates_repo.seed_starter_templates_if_empty()
    assert re_seeded is False
    names_after = {r["name"] for r in templates_repo.list_all()}
    assert victim["name"] not in names_after


def test_write_starter_templates_skip_existing_layers_in_missing(tmp_db):
    """The CLI path: insert any starter whose name is not already taken,
    leave the rest alone."""
    from app.wiki import templates as templates_repo

    bundled = templates_repo.iter_starter_templates()
    if not bundled:
        pytest.skip("no bundled starter templates available in this environment")

    # Pre-create one template with a colliding name, custom body.
    collision = bundled[0]
    templates_repo.create(
        name=collision["name"],
        body="user-customized body — do not overwrite",
        description="user-set",
        system_prompt=None,
        created_by_user_id=None,
    )

    inserted = templates_repo.write_starter_templates(skip_existing=True)

    assert inserted == len(bundled) - 1
    rows = {r["name"]: r for r in templates_repo.list_all()}
    # The pre-existing one keeps its user-customized body.
    assert rows[collision["name"]]["body"] == "user-customized body — do not overwrite"
    # Everything else from the bundle landed.
    for row in bundled[1:]:
        assert rows[row["name"]]["body"] == row["body"]
