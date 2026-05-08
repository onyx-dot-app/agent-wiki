"""Tests for the markdown broken-link detector."""
from __future__ import annotations

from pathlib import Path

from app.wiki import links


def test_resolves_and_finds_broken(tmp_repo, tmp_config):
    Path(tmp_config.wiki_dir, "auth").mkdir()
    Path(tmp_config.wiki_dir, "auth/passwords.md").write_text("# pw\n")

    body = (
        "See [pw](passwords.md) for the OK link.\n"
        "And [missing](other.md) — broken.\n"
        "Cross-dir [up](../index.md) — also broken.\n"
        "External [g](https://google.com) — skipped.\n"
        "Anchor only [self](#section) — skipped.\n"
        "Image ![alt](image.png) — skipped.\n"
    )
    out = links.find_broken_links(body, "auth/foo.md")
    targets = sorted(b.target for b in out)
    assert targets == ["../index.md", "other.md"]


def test_anchor_in_relative_link_validates_path(tmp_repo, tmp_config):
    Path(tmp_config.wiki_dir, "guide.md").write_text("# G\n")
    body = "See [g](guide.md#start) and [m](missing.md#section)."
    out = links.find_broken_links(body, "index.md")
    targets = [b.target for b in out]
    assert targets == ["missing.md#section"]


def test_traversal_outside_wiki_is_broken(tmp_repo):
    body = "Try to escape: [bad](../../etc/passwd.md)."
    out = links.find_broken_links(body, "auth/foo.md")
    assert len(out) == 1
    assert out[0].target == "../../etc/passwd.md"
