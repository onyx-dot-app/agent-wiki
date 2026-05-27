from app.wiki.diff import _word_diff


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
