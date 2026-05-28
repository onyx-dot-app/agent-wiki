import pytest

from app.models.file_system import DiffHunk, DiffLine
from app.wiki import git as wiki_git
from app.wiki.diff import _parse_unified, _promote_word_diff, _word_diff, parse_commit_diff


def test_word_diff_identical_strings() -> None:
    out = _word_diff("hello world", "hello world")
    assert out.prefix == "hello world"
    assert out.removed == ""
    assert out.added == ""
    assert out.suffix == ""


def test_word_diff_fully_disjoint() -> None:
    out = _word_diff("alpha beta", "gamma delta")
    assert out.prefix == ""
    assert out.removed == "alpha beta"
    assert out.added == "gamma delta"
    assert out.suffix == ""


def test_word_diff_common_prefix_only() -> None:
    out = _word_diff("hello old world", "hello new world")
    assert out.prefix == "hello "
    assert out.removed == "old"
    assert out.added == "new"
    assert out.suffix == " world"


def test_word_diff_common_prefix_no_suffix() -> None:
    out = _word_diff("foo bar baz", "foo qux")
    assert out.prefix == "foo "
    assert out.removed == "bar baz"
    assert out.added == "qux"
    assert out.suffix == ""


def test_word_diff_common_suffix_no_prefix() -> None:
    out = _word_diff("alpha end", "beta end")
    assert out.prefix == ""
    assert out.removed == "alpha"
    assert out.added == "beta"
    assert out.suffix == " end"


def test_word_diff_multi_word_added() -> None:
    out = _word_diff("hello world", "hello brave new world")
    assert out.prefix == "hello "
    assert out.suffix == " world"
    assert out.removed == ""
    assert out.added == "brave new"


def test_word_diff_leading_whitespace_in_added() -> None:
    out = _word_diff("world", " brave new world")
    assert out.prefix == " "
    assert out.removed == ""
    assert out.added == "brave new"
    assert out.suffix == " world"


def test_word_diff_leading_whitespace_in_removed() -> None:
    out = _word_diff(" brave new world", "world")
    assert out.prefix == " "
    assert out.removed == "brave new"
    assert out.added == ""
    assert out.suffix == " world"


def test_parse_unified_simple_modify() -> None:
    text = (
        "diff --git a/foo.md b/foo.md\n"
        "index abc..def 100644\n"
        "--- a/foo.md\n"
        "+++ b/foo.md\n"
        "@@ -1,3 +1,3 @@\n"
        " line one\n"
        "-old line\n"
        "+new line\n"
        " line three\n"
    )
    hunks = _parse_unified(text)
    assert len(hunks) == 1
    h = hunks[0]
    assert h.old_start == 1 and h.old_count == 3
    assert h.new_start == 1 and h.new_count == 3
    kinds = [line.kind for line in h.lines]
    assert kinds == ["context", "remove", "add", "context"]
    assert h.lines[0].old_lineno == 1 and h.lines[0].new_lineno == 1
    assert h.lines[1].old_lineno == 2 and h.lines[1].new_lineno is None
    assert h.lines[2].old_lineno is None and h.lines[2].new_lineno == 2
    assert h.lines[3].old_lineno == 3 and h.lines[3].new_lineno == 3


def test_parse_unified_short_header_form() -> None:
    # @@ -1 +1 @@ — count defaults to 1 when omitted
    text = "--- a/foo.md\n+++ b/foo.md\n@@ -1 +1 @@\n-only\n+changed\n"
    hunks = _parse_unified(text)
    assert len(hunks) == 1
    assert hunks[0].old_count == 1 and hunks[0].new_count == 1


def test_parse_unified_multi_hunk() -> None:
    text = "--- a/foo.md\n+++ b/foo.md\n@@ -1,1 +1,1 @@\n-a\n+b\n@@ -10,1 +10,1 @@\n-c\n+d\n"
    hunks = _parse_unified(text)
    assert len(hunks) == 2
    assert hunks[0].old_start == 1
    assert hunks[1].old_start == 10


def test_parse_unified_creation_only_adds() -> None:
    text = (
        "diff --git a/foo.md b/foo.md\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/foo.md\n"
        "@@ -0,0 +1,2 @@\n"
        "+line one\n"
        "+line two\n"
    )
    hunks = _parse_unified(text)
    assert len(hunks) == 1
    assert all(line.kind == "add" for line in hunks[0].lines)
    assert hunks[0].old_count == 0


def test_parse_unified_empty_returns_no_hunks() -> None:
    assert _parse_unified("") == []


def _make_hunk(*lines: DiffLine) -> DiffHunk:
    return DiffHunk(old_start=1, old_count=1, new_start=1, new_count=1, lines=list(lines))


def test_promote_word_diff_one_remove_one_add() -> None:
    hunk = _make_hunk(
        DiffLine(kind="context", text="ctx", word_diff=None, old_lineno=1, new_lineno=1),
        DiffLine(
            kind="remove", text="hello old world", word_diff=None, old_lineno=2, new_lineno=None
        ),
        DiffLine(kind="add", text="hello new world", word_diff=None, old_lineno=None, new_lineno=2),
        DiffLine(kind="context", text="tail", word_diff=None, old_lineno=3, new_lineno=3),
    )
    out = _promote_word_diff(hunk)
    kinds = [line.kind for line in out.lines]
    assert kinds == ["context", "word", "context"]
    assert out.lines[1].word_diff is not None
    assert out.lines[1].word_diff.removed == "old"
    assert out.lines[1].word_diff.added == "new"


def test_promote_word_diff_two_removes_no_promote() -> None:
    hunk = _make_hunk(
        DiffLine(kind="remove", text="one", word_diff=None, old_lineno=1, new_lineno=None),
        DiffLine(kind="remove", text="two", word_diff=None, old_lineno=2, new_lineno=None),
        DiffLine(kind="add", text="three", word_diff=None, old_lineno=None, new_lineno=1),
    )
    out = _promote_word_diff(hunk)
    kinds = [line.kind for line in out.lines]
    assert kinds == ["remove", "remove", "add"]


def test_promote_word_diff_remove_not_adjacent_to_add_no_promote() -> None:
    hunk = _make_hunk(
        DiffLine(kind="remove", text="x", word_diff=None, old_lineno=1, new_lineno=None),
        DiffLine(kind="context", text="y", word_diff=None, old_lineno=2, new_lineno=1),
        DiffLine(kind="add", text="z", word_diff=None, old_lineno=None, new_lineno=2),
    )
    out = _promote_word_diff(hunk)
    kinds = [line.kind for line in out.lines]
    assert kinds == ["remove", "context", "add"]


def test_promote_word_diff_two_independent_edit_blocks_both_promote() -> None:
    hunk = _make_hunk(
        DiffLine(kind="context", text="ctx1", word_diff=None, old_lineno=1, new_lineno=1),
        DiffLine(
            kind="remove", text="hello old world", word_diff=None, old_lineno=2, new_lineno=None
        ),
        DiffLine(kind="add", text="hello new world", word_diff=None, old_lineno=None, new_lineno=2),
        DiffLine(kind="context", text="ctx2", word_diff=None, old_lineno=3, new_lineno=3),
        DiffLine(kind="remove", text="foo bar", word_diff=None, old_lineno=4, new_lineno=None),
        DiffLine(kind="add", text="foo qux", word_diff=None, old_lineno=None, new_lineno=4),
        DiffLine(kind="context", text="ctx3", word_diff=None, old_lineno=5, new_lineno=5),
    )
    out = _promote_word_diff(hunk)
    kinds = [line.kind for line in out.lines]
    assert kinds == ["context", "word", "context", "word", "context"]
    assert out.lines[1].word_diff is not None
    assert out.lines[1].word_diff.removed == "old"
    assert out.lines[1].word_diff.added == "new"
    assert out.lines[3].word_diff is not None
    assert out.lines[3].word_diff.removed == "bar"
    assert out.lines[3].word_diff.added == "qux"


@pytest.fixture
def doc_with_two_commits(tmp_repo: None) -> tuple[str, str, str]:
    """Two commits on the same path. Returns (path, first_sha, second_sha)."""
    rel = "notes/page.md"
    first = wiki_git.commit_file(rel, "line one\nline two\nline three\n", "create", author=None)
    second = wiki_git.commit_file(rel, "line one\nline TWO\nline three\n", "edit", author=None)
    return rel, first, second


def test_parse_commit_diff_modify(doc_with_two_commits: tuple[str, str, str]) -> None:
    rel, _first, second = doc_with_two_commits
    out = parse_commit_diff(second, rel)
    assert out.path == rel
    assert out.sha == second
    assert out.parent_sha is not None
    assert out.is_creation is False
    assert len(out.hunks) >= 1
    word_lines = [line for hunk in out.hunks for line in hunk.lines if line.kind == "word"]
    assert len(word_lines) == 1
    assert word_lines[0].word_diff is not None
    assert word_lines[0].word_diff.removed == "two"
    assert word_lines[0].word_diff.added == "TWO"


def test_parse_commit_diff_creation(doc_with_two_commits: tuple[str, str, str]) -> None:
    rel, first, _second = doc_with_two_commits
    out = parse_commit_diff(first, rel)
    assert out.parent_sha is None
    assert out.is_creation is True
    assert out.hunks  # at least one
    assert all(line.kind == "add" for hunk in out.hunks for line in hunk.lines)
