"""Handler for the `explain_functionality` tool. Spec lives in `explain_functionality.json`.

Returns the static help blurb from ``app/llm/prompts/app_help.md``.
Edit that file to change what the agent tells users about the product.
"""
from __future__ import annotations

from typing import Any

from app.llm.prompts import load_prompt


def handle(args: dict[str, Any]) -> Any:
    return {"content": load_prompt("app_help")}
