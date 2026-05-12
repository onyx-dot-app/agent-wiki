"""Compose the first-turn prompt the spawned CLI sees at t=0.

First-turn only — never replayed on resume (the CLI already has its
conversation). Persisted to ``agent_sessions.first_turn_prompt`` so
debug + audit can see exactly what the agent originally saw.

Total prompt capped at ~256KB to stay under ARG_MAX and avoid pathological
page-body sizes (R4#2 audit fix). When the body would push us over the
cap, the body itself is truncated with an explicit ``[truncated]``
marker so headers + user message survive intact.
"""

from __future__ import annotations

_MAX_PROMPT_BYTES = 256 * 1024
_TRUNCATION_MARKER = "\n\n[truncated]"


def build_first_turn_prompt(
    *,
    wiki_path: str | None,
    page_body: str | None,
    working_dir: str | None,
    linked_repos: list[str],
    user_message: str,
) -> str:
    out = _compose(
        wiki_path=wiki_path,
        page_body=page_body,
        working_dir=working_dir,
        linked_repos=linked_repos,
        user_message=user_message,
    )
    if len(out.encode("utf-8")) <= _MAX_PROMPT_BYTES:
        return out

    # Over the cap. Recompose with body truncated such that the total
    # fits. The other parts are tiny relative to body, so we estimate
    # the body budget by recomposing without body and seeing the
    # overhead.
    headerless = _compose(
        wiki_path=wiki_path,
        page_body=None,
        working_dir=working_dir,
        linked_repos=linked_repos,
        user_message=user_message,
    )
    overhead = len(headerless.encode("utf-8"))
    # 16 bytes slack + marker length.
    marker_len = len(_TRUNCATION_MARKER.encode("utf-8"))
    body_budget = _MAX_PROMPT_BYTES - overhead - marker_len - 16
    if body_budget <= 0 or page_body is None:
        return out[: _MAX_PROMPT_BYTES - marker_len] + _TRUNCATION_MARKER

    body_bytes = page_body.encode("utf-8")
    truncated_body = body_bytes[:body_budget].decode("utf-8", errors="ignore") + _TRUNCATION_MARKER
    return _compose(
        wiki_path=wiki_path,
        page_body=truncated_body,
        working_dir=working_dir,
        linked_repos=linked_repos,
        user_message=user_message,
    )


def _compose(
    *,
    wiki_path: str | None,
    page_body: str | None,
    working_dir: str | None,
    linked_repos: list[str],
    user_message: str,
) -> str:
    parts: list[str] = []
    if wiki_path:
        parts.append(f"WIKI_PATH: {wiki_path}")
    if working_dir:
        parts.append(f"WORKING_DIRECTORY: {working_dir}")
    if linked_repos:
        parts.append("LINKED_REPOS:")
        for r in linked_repos:
            parts.append(f"  - {r}")
    if page_body:
        parts.append("")
        parts.append("PAGE_BODY:")
        parts.append(page_body)
    parts.append("")
    parts.append("USER_MESSAGE:")
    parts.append(user_message)
    return "\n".join(parts)
