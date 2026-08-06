"""One completion, parsed as a JSON object — shared by the corpus-derivation steps.

Extracted from ``entity_types`` rather than copied, because three of the behaviours here are
subtle enough that a second copy would quietly drift from them:

  - **Truncation is not a parse failure.** A response cut off at the output cap arrives as
    invalid JSON, and reporting it that way sends you looking for a prompt bug. The fix is a
    bigger cap or a smaller input, so it is reported as its own thing.
  - **The retry is not spent on truncation.** Re-prompting produces the same overflowing
    response, so the retry exists only for malformed JSON, and feeds the error back.
  - **Failure returns None.** One bad page or group must not abort a derivation that has already
    paid for every other call in it.

``module`` prefixes the log lines so a failure is attributable to the step that made the call, and
``ctx`` names what was being derived — without it the log said only "unparseable JSON (N chars)",
which identifies neither the input nor the reason.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.llm import client

log = logging.getLogger(__name__)

# Well above the client's 4096 default. These steps enumerate their input rather than summarizing
# it, so a response scales with the corpus slice it was given: measured over a 137-page production
# wiki, the worst page produced ~8.8k output tokens against a p90 of ~2.4k. The default truncated
# exactly the dense inputs these steps exist to handle, and truncation lands as invalid JSON — so
# the input would retry, truncate again, and be recorded as having found nothing. The cap costs
# nothing when unused, since billing follows tokens generated.
MAX_OUTPUT_TOKENS = 16384

JSON_RETRIES = 1


def is_truncated(stop_reason: str) -> bool:
    """Whether a response was cut short rather than finished.

    Each provider passes its own vocabulary through: "max_tokens" (anthropic, bedrock, and
    gemini's MAX_TOKENS), "length" (ollama, custom), and "incomplete" — what the OpenAI Responses
    API reports via status, and what ``client.complete`` reports when a stream ends with no
    terminal event at all.
    """
    lowered = stop_reason.lower()
    return any(token in lowered for token in ("max_token", "length", "incomplete"))


def complete_json(
    system: str,
    user: str,
    *,
    model: str | None,
    ctx: str = "",
    module: str = "ingest",
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> dict[str, Any] | None:
    """One completion parsed as a JSON object, or None on any failure."""
    label = ctx or "(unknown)"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error: str | None = None

    for attempt in range(JSON_RETRIES + 1):
        convo = (
            messages
            if last_error is None
            else [
                *messages[:-1],
                {
                    "role": "user",
                    "content": (
                        f"{user}\n\nYOUR PREVIOUS OUTPUT WAS REJECTED: {last_error}\n"
                        "Return corrected JSON."
                    ),
                },
            ]
        )
        try:
            result = client.complete(convo, model=model, max_tokens=max_tokens)
        except Exception:
            log.exception("%s: completion failed for %s", module, label)
            return None

        text = (result.text or "").strip()
        if is_truncated(result.stop_reason):
            # Distinct from a parse failure because the fix is distinct: raise the cap (or split
            # the input), rather than ask again. Returning here also spares the retry, which
            # would truncate identically.
            log.warning(
                "%s: response for %s was cut off at the %d-token output cap "
                "(stop_reason=%r, %d chars) — its referents are incomplete and were dropped",
                module,
                label,
                max_tokens,
                result.stop_reason,
                len(text),
            )
            return None

        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            last_error = "response was not a JSON object"
        else:
            try:
                parsed = cast(object, json.loads(text[start : end + 1]))
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON ({exc.msg})"
            else:
                if isinstance(parsed, dict):
                    return cast(dict[str, Any], parsed)
                last_error = "top-level JSON value was not an object"

        if attempt == JSON_RETRIES:
            log.warning(
                "%s: giving up on %s after %d attempt(s) — %s (%d chars)",
                module,
                label,
                attempt + 1,
                last_error,
                len(text),
            )
    return None


def member_indices(entry: dict[str, Any], upper: int) -> list[int]:
    """The 1-based ``member_indices`` of one output entry, as valid 0-based positions.

    Out-of-range and non-integer values are dropped rather than raising: a model that hallucinates
    one index should cost that member, not the whole response.

    A FRACTIONAL index is dropped rather than truncated. ``int(1.5)`` is 1, a perfectly valid
    position — so truncating would silently attach a DIFFERENT member than any the model named,
    which is worse than losing one: the response looks well-formed and the mistake is invisible.
    ``1.0`` is kept, because JSON has one number type and that is how an integer arrives.
    """
    raw = entry.get("member_indices")
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in cast(list[Any], raw):
        # ``bool`` is an ``int`` in Python, so ``True`` would silently claim member 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and not value.is_integer():
            continue
        index = int(value)
        if 1 <= index <= upper:
            out.append(index - 1)
    return out
