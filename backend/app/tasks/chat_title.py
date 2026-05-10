"""Generate a chat session title from its first user/assistant pair.

Runs on the ``documents_queue`` because that's the queue dedicated to LLM
work — we'd rather reuse its single LLM-bound worker than spin up a new
container for one short ``client.complete`` call. Background-tasks doc
notes this in ``local_data/wiki/background-tasks/``.

Failures are non-fatal: we log and leave ``chat_sessions.title`` NULL,
and the frontend falls back to displaying the first user message.
"""
from __future__ import annotations

import logging
from typing import Any

from app.chat import sessions as sessions_repo
from app.llm import client
from app.llm.errors import LLMError
from app.tasks.queues import documents_queue
from app.tracing import trace_flow

log = logging.getLogger(__name__)


_TITLE_SYSTEM_PROMPT = (
    "You write short conversation titles. Given a user's question and the "
    "assistant's reply, return a concise 3-6 word title summarizing the "
    "conversation. Output the title text only — no quotes, no trailing "
    "punctuation, no preamble."
)

_MAX_TITLE_CHARS = 80


@documents_queue.task()
def generate_chat_title(session_id: str) -> None:
    messages = sessions_repo.get_messages(session_id)
    user_msg = next((m for m in messages if m["role"] == "user"), None)
    assistant_msg = next((m for m in messages if m["role"] == "assistant"), None)
    if user_msg is None or assistant_msg is None:
        log.warning(
            "generate_chat_title session_id=%s: missing user/assistant pair",
            session_id,
        )
        return

    prompt: list[dict[str, Any]] = [
        {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Message 1, User:\n```\n{user_msg['content']}\n```\n\n"
                f"Message 2, Assistant:\n```\n{assistant_msg['content']}\n```\n\n"
                "Write a concise 3-6 word title summarizing the conversation "
                "above. Output the title text only — no quotes, no trailing "
                "punctuation, no preamble.\n"
                "Title:"
            ),
        },
    ]

    try:
        with trace_flow("task.chat_title", chat_session_id=session_id):
            result = client.complete(prompt, max_tokens=64)
    except LLMError:
        log.exception("generate_chat_title llm error session_id=%s", session_id)
        return

    title = _clean(result.text)
    if not title:
        log.warning("generate_chat_title session_id=%s: empty title", session_id)
        return

    sessions_repo.update_title(session_id, title)
    log.info("generate_chat_title session_id=%s title=%r", session_id, title)


def _clean(raw: str) -> str:
    title = raw.strip()
    # Strip surrounding quotes the model sometimes adds despite the prompt.
    if len(title) >= 2 and title[0] in "\"'" and title[-1] == title[0]:
        title = title[1:-1].strip()
    title = title.rstrip(".!?")
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS].rstrip()
    return title
