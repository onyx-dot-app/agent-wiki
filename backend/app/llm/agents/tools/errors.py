from __future__ import annotations


class ToolError(Exception):
    """Tool input was invalid or a precondition failed.

    The handler catches this and returns ``{"error": str(exc)}`` to the
    model instead of raising. Use for user-facing error messages — the
    string is shown to the LLM verbatim.
    """
