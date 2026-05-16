"""Tool registry for the chat agent.

Each tool is a `<name>.json` (function-call spec, in the normalized shape
`app.llm.client.complete` accepts) paired with a `<name>.py` module exposing
`handle(args: dict) -> Any`. Specs and handlers are loaded eagerly at import
time so a malformed JSON or a missing handler fails loud at startup.

A tool module may optionally expose ``available() -> bool`` to declare
whether it can run right now (e.g. depending on a configured API key).
Tools without an ``available`` are considered always available. The
chat/skills layer consults ``is_available`` before including a tool's spec
in what the LLM sees, so unconfigured tools aren't advertised.

To add a tool: drop a new pair in this directory. The spec's ``name`` MUST
match the filename stem and the handler module name.
"""
from __future__ import annotations

import json
import logging
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, cast

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent

TOOL_SPECS: list[dict[str, Any]] = []
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {}
_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {}
_AVAILABILITY: dict[str, Callable[[], bool]] = {}


def _always_available() -> bool:
    return True


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
        avail = getattr(module, "available", None)
        TOOL_SPECS.append(spec)
        _SPECS_BY_NAME[name] = spec
        _HANDLERS[name] = handler
        _AVAILABILITY[name] = (
            cast(Callable[[], bool], avail) if callable(avail) else _always_available
        )


def dispatch(name: str, args: dict[str, Any]) -> Any:
    fn = _HANDLERS.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    return fn(args)


def spec_by_name(name: str) -> dict[str, Any]:
    """Return the JSON spec for a tool by name, or raise if unknown.

    Used by the skills registry to resolve `tool_names` lists into the actual
    spec dicts that get sent to the LLM. Does NOT consult availability —
    callers that want to filter unavailable tools call ``is_available``.
    """
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"unknown tool: {name}")
    return spec


def is_available(name: str) -> bool:
    """Whether ``name`` is currently runnable.

    A tool reports unavailable when its prerequisites aren't satisfied —
    today that's API-key configuration for ``web_search`` / ``open_urls``.
    Used to prune the tool list shown to the LLM so it doesn't try to call
    something that would immediately fail.

    Unknown names are unavailable. Exceptions raised by a tool's ``available``
    check are swallowed (logged at warning) and treated as unavailable —
    the spec build path shouldn't be able to crash on a flaky check.
    """
    check = _AVAILABILITY.get(name)
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        log.warning("availability check raised for tool=%s", name, exc_info=True)
        return False


_load_all()
