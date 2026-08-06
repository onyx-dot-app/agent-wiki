"""The prompt directory, as a contract rather than a habit.

``load_prompt`` takes a bare string and reads a file, so a typo is not a failure until the moment
that prompt is used — which, for the corpus-derivation steps, is deep inside a paid LLM pass. And
the naming drifted three times before it was written down: four files named for a STAGE with no
role, one caller split across dot-segments while another used the same dots for the role.

These pin the two things that keep it honest: every prompt file says which message it is, and
every name a caller asks for exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_PROMPTS = _BACKEND / "app" / "llm" / "prompts"

# The second segment names WHICH MESSAGE the file is. ``system`` is the instruction; the rest are
# the user-message fragments a caller composes (``ingest_selector`` uses two of them in one call,
# which is why they need distinct names rather than a single "user").
_ROLES = {"system", "doc", "candidates", "input"}

# Not a prompt: ``explain_functionality`` returns it as TOOL OUTPUT. It has no message role, and
# giving it one would assert something false. Listed here so the exception is deliberate.
_NOT_A_PROMPT = {"app_help.md"}


def _prompt_files() -> list[Path]:
    return sorted(p for p in _PROMPTS.glob("*.md"))


def _requested_names() -> set[str]:
    """Every string passed to ``load_prompt`` anywhere in the app, by AST rather than regex."""
    names: set[str] = set()
    for path in (_BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called != "load_prompt" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def test_every_prompt_says_which_message_it_is() -> None:
    """``<caller>.<role>.md`` — exactly two segments, the last one a known message role.

    A stage belongs in the caller, joined by an underscore: ``entity_types_merge.system.md``, not
    ``entity_types.merge.md``. Otherwise the last segment answers "which step" for some files and
    "which message" for others, and neither can be read at a glance.
    """
    wrong = [
        p.name
        for p in _prompt_files()
        if p.name not in _NOT_A_PROMPT
        and (len(p.name.split(".")) != 3 or p.name.split(".")[1] not in _ROLES)
    ]

    assert not wrong, (
        f"prompt file(s) not named <caller>.<role>.md with role in {sorted(_ROLES)}: {wrong}"
    )


def test_every_name_a_caller_asks_for_exists() -> None:
    """A typo'd prompt name is a crash at the moment of use, not at import — and for a derivation
    step that means partway through a pass that has already paid for other calls."""
    missing = sorted(name for name in _requested_names() if not (_PROMPTS / f"{name}.md").is_file())

    assert not missing, f"load_prompt() names with no file: {missing}"


def test_no_prompt_file_is_orphaned() -> None:
    """A prompt nobody loads is either a rename that left its old copy behind, or a caller that
    silently stopped using it. Both are worth seeing."""
    requested = _requested_names()
    orphans = [
        p.name
        for p in _prompt_files()
        if p.name not in _NOT_A_PROMPT and p.name.removesuffix(".md") not in requested
    ]

    assert not orphans, f"prompt file(s) no caller loads: {orphans}"
