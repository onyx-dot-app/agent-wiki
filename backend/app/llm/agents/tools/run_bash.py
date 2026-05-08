"""Handler for the `run_bash` tool. Spec lives in `run_bash.json`.

Thin shim over ``_bash.run`` — the heavy lifting (parse / allowlist /
execute / truncate) is in ``_bash.py``.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _bash


def handle(args: dict[str, Any]) -> Any:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"error": "command is required"}

    result = _bash.run(command.strip())
    return {
        "output": result.output,
        "exit_code": result.exit_code,
        "elapsed_ms": result.elapsed_ms,
        "truncated": result.truncated,
    }
