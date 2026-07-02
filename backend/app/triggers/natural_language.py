"""LLM-backed trigger evaluator and message renderer.

Two paths, picked by the fan-out task:

**Standard path** — for doc-scoped triggers, and directory-scoped triggers
on edits. Two phases:

* ``matches(nl_description, payload)`` — phase 1, a single tool call that
  returns ``{matches: bool, reason: str}``. Decides whether the change
  satisfies the trigger's firing condition.
* ``render_message(message_instruction, payload, *, reason)`` — phase 2,
  only run when phase 1 matched. A second tool call that returns the
  final notification text the user (or downstream system) sees.

The shared ``payload`` (built by ``app.triggers.diff.build_payload``) is
the docs under the trigger's scope followed by a ``+/-`` view of the
changed doc. The eval prompt tells the model to focus on the diff unless
the description is clearly about overall state.

**New-file-in-dir path** — for directory-scoped triggers when a brand-new
file appears under the scope. The diff would just be the body with ``+``
on every line, so we skip it entirely:

* ``evaluate_new_file_in_dir(nl_description, message_instruction, payload)``
  — single ``complete()`` call that emits a JSON object
  ``{"triggered": bool, "trigger_message": str}`` directly in the
  assistant text. Combines firing-condition check and message render
  into one round-trip.

The new-file payload (``app.triggers.diff.build_new_file_payload``) is
the scoped docs followed by the new file's path and full body — no
diff section.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

from pydantic import BaseModel

from app.llm.client import complete
from app.llm.errors import LLMError
from app.tracing import trace_flow

log = logging.getLogger(__name__)


class MatchResult(BaseModel):
    """Result of a phase-1 trigger evaluation: did the change satisfy the
    NL "if"? ``reason`` is a one-liner suitable for the events log."""

    matched: bool
    reason: str


class NewFileEvalResult(BaseModel):
    """Result of the combined evaluate+render call for new files under a
    directory scope. ``message`` is the already-rendered notification."""

    triggered: bool
    message: str

# --------------------------------------------------------------------------- #
# Phase 1: does the change satisfy the trigger?                               #
# --------------------------------------------------------------------------- #

_EVAL_SYSTEM_PROMPT = """\
You evaluate whether a wiki document change satisfies a natural-language \
trigger description.

The user message gives you, in order:
  1. The trigger description ("if …").
  2. A SCOPED DOCS block: the current bodies of the document(s) under \
the trigger's scope (context on how the changed doc relates to its \
neighbors).
  3. A CHANGE block with the path, change kind, and the +/- diff (or full \
body, for new files / wholesale rewrites).

How to evaluate:
  * Triggers should typically be evaluated against the **diff** — what \
was added, removed, or rewritten in this change. The scoped docs are \
context, not the primary signal.
  * Only evaluate against the overall current state of the document(s) \
when the trigger description is clearly about state rather than an \
update — for example, "when status is yellow" (state) vs. "when status \
flips to yellow" (update). When in doubt, evaluate the diff.
  * Be conservative: false positives are louder than false negatives. \
If the change doesn't clearly satisfy the description, say no.
  * Use only what is in the payload below. Do not bring in outside \
knowledge or speculate.
  * The reason must quote or paraphrase the specific change (or specific \
state) that satisfies the trigger.

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


def matches(nl_description: str, payload: str) -> MatchResult:
    """Phase 1: did the change satisfy the trigger's NL description?

    ``payload`` is the combined scoped-docs + change view from
    ``app.triggers.diff.build_payload``.
    """
    user_msg = f"Trigger description (if):\n{nl_description}\n\n{payload}"
    try:
        with trace_flow("trigger.matches"):
            resp = complete(
                [
                    {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                tools=[_REPORT_TOOL],
                max_tokens=512,
            )
    except LLMError as e:
        log.warning("trigger eval llm_error code=%s msg=%s", e.code, e.message)
        return MatchResult(matched=False, reason=f"llm_error: {e.code}")

    for call in resp.tool_calls:
        if call.name == "report":
            return MatchResult(
                matched=bool(call.arguments.get("matches")),
                reason=str(call.arguments.get("reason") or ""),
            )
    return MatchResult(matched=False, reason="no_tool_call")


# --------------------------------------------------------------------------- #
# Phase 2: render the delivered message                                       #
# --------------------------------------------------------------------------- #

_RENDER_SYSTEM_PROMPT = """\
You compose the notification message that a wiki trigger delivers when \
it fires. The trigger's owner has already written a short instruction \
describing what they want the message to say; your job is to produce \
the final text a human (or downstream system) will see.

The user message gives you, in order:
  1. The owner's message instruction ("send …").
  2. A one-line "match reason" produced by the firing-condition check.
  3. A SCOPED DOCS block: the current bodies of the document(s) under \
the trigger's scope (context).
  4. A CHANGE block with the path, change kind, and the +/- diff.

Guidance:
  * Follow the owner's instruction. Keep the message concise and \
specific — quote concrete values from the change where useful.
  * Ground the message in the diff first; treat the scoped docs as \
context. If the instruction is clearly about overall state, you can \
reference the latest version directly.
  * Do not include meta-commentary ("the trigger fired because…"), \
internal IDs, or explanations of your reasoning. Output only the \
delivered message text.
  * Plain text or markdown is fine. No greetings, no signoff.
  * Delivery appends its own attribution line naming the source \
document — do not add a title or repeat the document name.

Always respond by calling the `render` tool exactly once.\
"""

_RENDER_TOOL = {
    "name": "render",
    "description": "Return the final notification message text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The final notification text to deliver.",
            },
        },
        "required": ["message"],
    },
}


def render_message(
    message_instruction: str, payload: str, *, reason: str
) -> str:
    """Phase 2. Returns the rendered message text.

    On any failure we fall back to ``message_instruction`` itself so the
    Event Log still receives something the owner authored — better to
    surface the raw template than drop the fire.
    """
    user_msg = (
        f"Owner's message instruction:\n{message_instruction}\n\n"
        f"Match reason:\n{reason}\n\n"
        f"{payload}"
    )
    try:
        with trace_flow("trigger.render_message"):
            resp = complete(
                [
                    {"role": "system", "content": _RENDER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                tools=[_RENDER_TOOL],
                max_tokens=1024,
            )
    except LLMError as e:
        log.warning("trigger render llm_error code=%s msg=%s", e.code, e.message)
        return message_instruction

    for call in resp.tool_calls:
        if call.name == "render":
            rendered = str(call.arguments.get("message") or "").strip()
            if rendered:
                return rendered
    log.warning("trigger render: no tool call, falling back to instruction")
    return message_instruction


# --------------------------------------------------------------------------- #
# Schedule path — phase 1 (snapshot eval) + phase 2 (snapshot render)         #
# --------------------------------------------------------------------------- #

_SCHEDULE_EVAL_SYSTEM_PROMPT = """\
You evaluate whether a wiki satisfies a natural-language trigger \
description. The trigger fires on a schedule (cron), so it is evaluated \
periodically rather than on a single edit.

The user message gives you, in order:
  1. The trigger description ("if …").
  2. A SCOPED DOCS block: the full current bodies of the document(s) \
under the trigger's scope. This is the primary material.
  3. A CHANGES SINCE LAST CHECK block: the diffs (new files, edits, \
rewrites, deletions) committed under the trigger's scope since the \
previous scheduled check. It reads "(no changes in this window)" when \
nothing changed in the window.
  4. A SCHEDULED CHECK block naming the trigger's scope and the tick time.

How to evaluate:
  * If the trigger describes a CHANGE over time ("a new doc was added", \
"X was updated", "the status changed since last week"), evaluate it \
against the CHANGES SINCE LAST CHECK block. If that block reports no \
changes, such a trigger does NOT fire.
  * If the trigger describes overall STATE ("X is still marked blocked", \
"there is no owner listed"), evaluate it against the SCOPED DOCS block, \
focusing on the document(s) under the listed scope.
  * Be conservative: false positives are louder than false negatives. \
If the wiki doesn't clearly satisfy the description, say no.
  * Use only what is in the payload below. Do not bring in outside \
knowledge or speculate.
  * The reason must quote or paraphrase the specific change or state \
that satisfies the trigger.

Always respond by calling the `report` tool exactly once.\
"""


def matches_snapshot(nl_description: str, payload: str) -> MatchResult:
    """Phase 1 for schedule triggers: does current wiki state satisfy the
    trigger?

    ``payload`` is the scoped docs + CHANGES SINCE LAST CHECK diff +
    SCHEDULED CHECK block from ``app.triggers.diff.build_schedule_payload``.
    """
    user_msg = f"Trigger description (if):\n{nl_description}\n\n{payload}"
    try:
        with trace_flow("trigger.matches_snapshot"):
            resp = complete(
                [
                    {"role": "system", "content": _SCHEDULE_EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                tools=[_REPORT_TOOL],
                max_tokens=512,
            )
    except LLMError as e:
        log.warning("trigger schedule eval llm_error code=%s msg=%s", e.code, e.message)
        return MatchResult(matched=False, reason=f"llm_error: {e.code}")

    for call in resp.tool_calls:
        if call.name == "report":
            return MatchResult(
                matched=bool(call.arguments.get("matches")),
                reason=str(call.arguments.get("reason") or ""),
            )
    return MatchResult(matched=False, reason="no_tool_call")


_SCHEDULE_RENDER_SYSTEM_PROMPT = """\
You compose the notification message that a scheduled wiki trigger \
delivers when it fires. The trigger's owner has already written a short \
instruction describing what they want the message to say; your job is \
to produce the final text a human (or downstream system) will see.

The user message gives you, in order:
  1. The owner's message instruction ("send …").
  2. A one-line "match reason" produced by the firing-condition check.
  3. A SCOPED DOCS block: the full current bodies of the document(s) \
under the trigger's scope. Quote from here.
  4. A CHANGES SINCE LAST CHECK block: what changed under the scope since \
the previous check (may read "(no changes in this window)").
  5. A SCHEDULED CHECK block naming the trigger's scope and tick time.

Guidance:
  * Follow the owner's instruction. Keep the message concise and \
specific — quote concrete values from the wiki where useful.
  * Ground the message in the current wiki state and, when the \
instruction is about what changed, the CHANGES SINCE LAST CHECK block, \
scoped to the SCHEDULED CHECK scope.
  * Do not include meta-commentary ("the trigger fired because…"), \
internal IDs, or explanations of your reasoning. Output only the \
delivered message text.
  * Plain text or markdown is fine. No greetings, no signoff.
  * Delivery appends its own attribution line naming the source \
document — do not add a title or repeat the document name.

Always respond by calling the `render` tool exactly once.\
"""


def render_snapshot_message(
    message_instruction: str, payload: str, *, reason: str
) -> str:
    """Phase 2 for schedule triggers. Returns the rendered message text.

    On any failure we fall back to ``message_instruction`` itself so the
    Event Log still receives something the owner authored.
    """
    user_msg = (
        f"Owner's message instruction:\n{message_instruction}\n\n"
        f"Match reason:\n{reason}\n\n"
        f"{payload}"
    )
    try:
        with trace_flow("trigger.render_snapshot_message"):
            resp = complete(
                [
                    {"role": "system", "content": _SCHEDULE_RENDER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                tools=[_RENDER_TOOL],
                max_tokens=1024,
            )
    except LLMError as e:
        log.warning(
            "trigger schedule render llm_error code=%s msg=%s", e.code, e.message
        )
        return message_instruction

    for call in resp.tool_calls:
        if call.name == "render":
            rendered = str(call.arguments.get("message") or "").strip()
            if rendered:
                return rendered
    log.warning(
        "trigger schedule render: no tool call, falling back to instruction"
    )
    return message_instruction


# --------------------------------------------------------------------------- #
# New-file-in-dir path                                                        #
# --------------------------------------------------------------------------- #

_NEW_FILE_SYSTEM_PROMPT = """\
You evaluate a directory-scoped wiki trigger when a brand-new document \
has just been created under the scope, and — if it fires — write the \
notification message the owner asked for. This is a single combined \
check: there is no diff to inspect, because the file did not exist \
before.

The user message gives you, in order:
  1. The trigger description ("if …").
  2. The owner's message instruction ("send …").
  3. A SCOPED DOCS block: the current bodies of the document(s) under \
the trigger's scope (context).
  4. A NEW FILE block with the path and full body of the just-created \
document.

How to evaluate:
  * Decide ``triggered`` by reading the new file. Use the scoped docs \
only as context — the primary signal is the new file's content.
  * Be conservative: if the file does not clearly satisfy the trigger \
description, set ``triggered`` to false.
  * Use only what is in the payload. Do not bring in outside knowledge.

If triggered, write ``trigger_message`` following the owner's \
instruction, grounded in the new file. Concise and specific. No \
greetings, signoff, meta-commentary, or reasoning. If not triggered, \
set ``trigger_message`` to "".

Respond with a single JSON object as your entire response, exactly in \
this shape:

  {"triggered": true, "trigger_message": "..."}

Hard rules:
  * Output JSON only. No prose, no markdown fences, no commentary \
before or after the object.
  * ``triggered`` must be a JSON boolean (true / false).
  * ``trigger_message`` must be a JSON string.\
"""

# Pulls the first balanced JSON object out of a response. Tolerates a
# small amount of stray text or markdown code fences before/after — model
# output can drift even with strict instructions.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    try:
        data: Any = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def evaluate_new_file_in_dir(
    nl_description: str, message_instruction: str, payload: str
) -> NewFileEvalResult:
    """Single-call combined evaluate + render for the new-file-in-dir case.

    ``payload`` is the scoped docs + NEW FILE block from
    ``app.triggers.diff.build_new_file_payload``.

    On LLM error or unparseable output, returns ``triggered=False`` with an
    empty message — better to drop a fire than to send a confusing one.
    """
    user_msg = (
        f"Trigger description (if):\n{nl_description}\n\n"
        f"Message instruction (send):\n{message_instruction}\n\n"
        f"{payload}"
    )
    try:
        with trace_flow("trigger.evaluate_new_file_in_dir"):
            resp = complete(
                [
                    {"role": "system", "content": _NEW_FILE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=1024,
            )
    except LLMError as e:
        log.warning(
            "trigger new-file-in-dir llm_error code=%s msg=%s", e.code, e.message
        )
        return NewFileEvalResult(triggered=False, message="")

    text = resp.text.strip()
    data = _extract_json_object(text)
    if data is None:
        log.warning("trigger new-file-in-dir: unparseable response: %r", text[:200])
        return NewFileEvalResult(triggered=False, message="")

    triggered = bool(data.get("triggered"))
    trigger_message = str(data.get("trigger_message") or "").strip()
    if triggered and not trigger_message:
        # Owner's instruction is the safe fallback so the Event Log isn't
        # blank when the model says yes but forgets the message.
        trigger_message = message_instruction
    return NewFileEvalResult(triggered=triggered, message=trigger_message)
