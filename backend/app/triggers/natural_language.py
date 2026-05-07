"""LLM-backed evaluator: 'does this doc delta satisfy this NL trigger?'.

Tight, single-shot tool call returning ``{matches: bool, reason: str}``. Runs
hot — every commit fans out to one of these per matching trigger — so keep
the prompt small and use the configured cheap model.
"""
from __future__ import annotations

from app.llm.client import LLMError, complete

_SYSTEM_PROMPT = """\
You evaluate whether a wiki document change satisfies a natural-language \
trigger description. Be conservative: false positives are louder than false \
negatives. Only use information present in the BEFORE and AFTER snippets. Do \
not bring in outside knowledge or speculate. The reason must quote or \
paraphrase the specific change you observed.

Always respond by calling the `report` tool exactly once.\
"""

_REPORT_TOOL = {
    "name": "report",
    "description": "Report whether the change matches the trigger description.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "boolean",
                "description": "True if the change satisfies the trigger description.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence citing the specific change. "
                    "If matches=false, briefly say what was missing."
                ),
            },
        },
        "required": ["matches", "reason"],
    },
}


def matches(
    nl_description: str,
    before_body: str,
    after_body: str,
    *,
    change_kind: str,
) -> tuple[bool, str]:
    """Run a single LLM call. Returns ``(matched, reason)``.

    ``change_kind`` is "create", "edit", or "new_file_in_dir" — the model
    uses it to decide how to interpret the BEFORE side (empty / prior body /
    sibling docs).
    """
    user_msg = (
        f"Trigger description:\n{nl_description}\n\n"
        f"Change kind: {change_kind}\n\n"
        f"BEFORE:\n{before_body or '(empty — file did not exist)'}\n\n"
        f"AFTER:\n{after_body or '(empty)'}"
    )

    try:
        resp = complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            tools=[_REPORT_TOOL],
            max_tokens=512,
        )
    except LLMError as e:
        return False, f"llm_error: {e.code}"

    for call in resp.get("tool_calls") or []:
        if call.get("name") == "report":
            args = call.get("arguments") or {}
            return bool(args.get("matches")), str(args.get("reason") or "")
    return False, "no_tool_call"
