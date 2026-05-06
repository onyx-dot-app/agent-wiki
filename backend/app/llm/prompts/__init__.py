"""Prompt loader. Prompts are kept as ``.md`` siblings of this file."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text()
