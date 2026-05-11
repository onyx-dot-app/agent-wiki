"""Shared HTTP-shape helpers for the API routers.

* :class:`ErrorResponse` is the standard ``{"error": "..."}`` envelope
  every 4xx/5xx body matches.
* :class:`QueueFullErrorResponse` is the 503 body the task-queue
  backpressure path returns.
* :class:`RequestError` is raised by helpers (notably
  :func:`parse_body`) to surface "bad input" with a status code; the
  app-level exception handler in ``app.main`` translates it into the
  envelope above.

FastAPI's body-binding (typed Pydantic parameter on a route) handles
most validation cases natively. :func:`parse_body` is here for routes
that accept a raw ``dict[str, Any]`` body and want field presence
information that strict Pydantic models hide (see ``put_llm`` for the
canonical example).
"""
from __future__ import annotations

from typing import TypeVar

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
    """Validate a JSON body against ``model_cls``. Raise RequestError
    on failure."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RequestError("request body must be a JSON object")
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise RequestError(_format_error(exc)) from exc


def _format_error(exc: ValidationError) -> str:
    """First validation error, formatted for end users.

    Pydantic's full error report is too noisy for an API
    ``{"error": ...}``; we surface just the first failing field plus
    its message.
    """
    err = exc.errors()[0]
    loc = ".".join(str(x) for x in err.get("loc", []))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
