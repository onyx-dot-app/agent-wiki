"""Helpers for request/response model handling in Flask blueprints.

Pattern in routes:

    req = parse_body(MyRequest, request.get_json(silent=True))
    ...
    return jsonify(MyResponse(...).model_dump()), 200

``parse_body`` raises ``RequestError`` on invalid input; an error handler
in ``app.main`` converts it into ``ErrorResponse`` with the right status.
For inline error returns (404, 409, etc.), call ``error(...)``.

Why not ``flask-pydantic``?
    The ``@validate()`` decorator looks tempting but it's a poor fit for a
    pyright-checked codebase: it injects validated args (``body``, ``query``,
    etc.) that the type checker can't see, so handlers either lose type info
    on those params or need explicit annotations that defeat the point. The
    explicit ``parse_body`` call below stays type-checkable end to end.
    If we ever want OpenAPI docs on top of this, ``APIFlask`` is a better
    fit than ``flask-pydantic`` for the same reason.
"""
from __future__ import annotations

from typing import Any, TypeVar

from flask import jsonify
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ErrorResponse(BaseModel):
    """Standard error envelope — every 4xx/5xx body matches this shape."""

    error: str


class QueueFullErrorResponse(BaseModel):
    """503 body when a producer hits a queue's configured cap."""

    error: str
    queue: str
    size: int
    limit: int


class RequestError(Exception):
    """Bad request — surfaced as ``ErrorResponse`` with ``status``."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def parse_body(model_cls: type[T], raw: object) -> T:
    """Validate a JSON body against ``model_cls``. Raise RequestError on failure."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RequestError("request body must be a JSON object")
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise RequestError(_format_error(exc)) from exc


def error(message: str, status: int = 400) -> tuple[Any, int]:
    """Build a typed ``ErrorResponse`` JSON response with the given status."""
    return jsonify(ErrorResponse(error=message).model_dump()), status


def _format_error(exc: ValidationError) -> str:
    """First validation error, formatted for end users.

    Pydantic's full error report is too noisy for an API ``{"error": ...}``;
    we surface just the first failing field plus its message.
    """
    err = exc.errors()[0]
    loc = ".".join(str(x) for x in err.get("loc", []))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
