"""Tool registry for the chat agent.

Each tool is a `<name>.json` (function-call spec, in the normalized shape
`app.llm.client.complete` accepts) paired with a `<name>.py` module exposing
`handle(args: dict) -> Any`. Specs and handlers are loaded eagerly at import
time so a malformed JSON or a missing handler fails loud at startup.

To add a tool: drop a new pair in this directory. The spec's ``name`` MUST
match the filename stem and the handler module name.
"""
from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

_DIR = Path(__file__).parent

TOOL_SPECS: list[dict[str, Any]] = []
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {}
_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def _load_all() -> None:
    for json_path in sorted(_DIR.glob("*.json")):
        spec = json.loads(json_path.read_text())
        name = spec.get("name")
        if not name:
            raise ValueError(f"{json_path.name}: spec missing 'name'")
        if name != json_path.stem:
            raise ValueError(
                f"{json_path.name}: spec name {name!r} must equal filename stem"
            )
        module = import_module(f"{__name__}.{name}")
        handler = getattr(module, "handle", None)
        if not callable(handler):
            raise ValueError(f"{name}: module is missing handle(args) callable")
        TOOL_SPECS.append(spec)
        _SPECS_BY_NAME[name] = spec
        _HANDLERS[name] = handler


def dispatch(name: str, args: dict[str, Any]) -> Any:
    fn = _HANDLERS.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    return fn(args)


def spec_by_name(name: str) -> dict[str, Any]:
    """Return the JSON spec for a tool by name, or raise if unknown.

    Used by the skills registry to resolve `tool_names` lists into the actual
    spec dicts that get sent to the LLM.
    """
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"unknown tool: {name}")
    return spec


_load_all()
