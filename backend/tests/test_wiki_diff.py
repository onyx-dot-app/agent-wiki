from app.wiki.diff import _parse_unified, _word_diff


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
