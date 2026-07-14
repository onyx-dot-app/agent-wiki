"""Contract for the shared PageKind enum.

PageKind is the single page-vs-folder representation across ACLs, update
policies, and Trash. These lock the two properties the refactor relies on:
`.of()` classification, and `str`-compatibility (equality + JSON value) so it
drops into str-typed columns/params and serializes as `"page"`/`"folder"`.
"""
from __future__ import annotations

import json

from app.models.wiki import PageKind


def test_of_classifies_by_extension():
    assert PageKind.of("notes/a.md") is PageKind.PAGE
    assert PageKind.of("notes") is PageKind.FOLDER
    assert PageKind.of("") is PageKind.FOLDER  # wiki root is a folder


def test_str_compatibility():
    # Equal to and interchangeable with the bare strings the DB stores.
    assert PageKind.PAGE == "page"
    assert PageKind.FOLDER == "folder"
    assert PageKind.PAGE in {"page", "folder"}
    assert PageKind("folder") is PageKind.FOLDER


def test_json_serializes_to_value():
    # Not "PageKind.PAGE" — API responses stay "page"/"folder".
    assert json.dumps(PageKind.PAGE) == '"page"'
