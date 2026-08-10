"""FastAPI port of ``app/api/chat.py``.

The 4 session-CRUD routes land in Phase 3 (plain JSON). The SSE
endpoint ``POST /api/chat/messages`` lands here in Phase 4 — async
streaming so the model worker isn't pinned to one OS thread per
in-flight chat the way the Flask sync-worker setup is today.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.auth import User
from app.auth import users as users_repo
from app.auth.deps import require_user
from app.chat import sessions as sessions_repo
from app.llm.agents.chat import (
    chat_agent_scope,
    messages_from_history,
    run_chat_stream,
)
from app.models.user_settings import UserSettings
from app.llm.errors import LLMError
from app.models.chat import (
    ChatMessageOut,
    ChatSessionDetail,
    ChatSessionOut,
    DraftingInitRequest,
    SendChatRequest,
    SetFeedbackRequest,
)
from app.tasks.chat_title import generate_chat_title
from app.tracing import trace_flow
from app.wiki import filesystem
from app.wiki import templates as wiki_templates

router = APIRouter()
log = logging.getLogger(__name__)


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")


def _session_out(
    row: dict[str, Any], *, touches_path: bool = False
) -> ChatSessionOut:
    return ChatSessionOut(
        id=row["id"],
        title=row["title"],
        touches_path=touches_path,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _safe_context_paths(context_paths: list[str]) -> list[str]:
    """Validate client-supplied context paths before prompt embedding. A value
    that could break the reminder framing or fails ``safe_rel_path`` is
    dropped, not rejected. Order is preserved and duplicates collapse."""
    safe: list[str] = []
    for path in context_paths:
        if not path or any(c in path for c in "\n\r<>"):
            continue
        try:
            rel = filesystem.safe_rel_path(path)
        except ValueError:
            continue
        if rel not in safe:
            safe.append(rel)
    return safe


def _inject_turn_context(
    messages: list[dict[str, Any]],
    user: User,
    context_paths: list[str],
    work_role: str | None,
) -> None:
    """Insert a user-role ``<system-reminder>`` naming the user and their
    attached pages just before the latest turn. Ephemeral, built fresh per
    request and never persisted."""
    who = f"{user.name} <{user.email}>" if user.name else user.email
    role = f", role: {work_role}" if work_role else ""
    paths = _safe_context_paths(context_paths)
    if len(paths) == 1:
        where = (
            f'They currently have the wiki page "{paths[0]}" open — read it '
            "with read_doc/read_page if their message is about it."
        )
    elif paths:
        listed = ", ".join(f'"{p}"' for p in paths)
        where = (
            f"They attached these wiki pages as context: {listed} — read them "
            "with read_doc/read_page if their message is about them."
        )
    else:
        where = "They are not on a specific wiki page right now."
    reminder = {
        "role": "user",
        "content": (
            f"<system-reminder>\nYou are chatting with {who}{role}. {where}\n"
            "</system-reminder>"
        ),
    }
    # Before the current (last) user turn so the agent reads context first.
    messages.insert(max(len(messages) - 1, 0), reminder)


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(
    path: str | None = None,
    user: User = Depends(require_user),
) -> list[ChatSessionOut]:
    """``path`` is the page the caller is viewing. Sessions whose turns worked
    on it come back flagged so the client can group them separately."""
    rows = sessions_repo.list_for_user(user.id)
    safe = _safe_context_paths([path] if path else [])
    touching = (
        sessions_repo.ids_touching_path(user.id, safe[0]) if safe else set[str]()
    )
    return [_session_out(r, touches_path=r["id"] in touching) for r in rows]


@router.post(
    "/sessions",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_session(user: User = Depends(require_user)) -> ChatSessionOut:
    row = sessions_repo.create(user.id)
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(
    session_id: str,
    user: User = Depends(require_user),
) -> ChatSessionDetail:
    row = sessions_repo.get(session_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = sessions_repo.get_messages(session_id)
    return ChatSessionDetail(
        session=ChatSessionOut(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ),
        messages=[
            ChatMessageOut(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                events=m["events"],
                feedback=m["feedback"],
                created_at=m["created_at"],
            )
            for m in messages
        ],
    )


@router.put(
    "/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT
)
def set_message_feedback(
    message_id: str,
    req: SetFeedbackRequest,
    user: User = Depends(require_user),
) -> Response:
    """Record advisory feedback without changing future agent answers."""
    if not sessions_repo.set_feedback(message_id, user.id, req.feedback):
        raise HTTPException(status_code=404, detail="message not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    user: User = Depends(require_user),
) -> Response:
    if not sessions_repo.delete(session_id, user.id):
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages")
async def send_message(
    req: SendChatRequest,
    request: Request,
    user: User = Depends(require_user),
) -> Response:
    """Run one user turn through the chat agent, streaming JSON-RPC-like
    SSE frames back to the client.

    The agent generator (``run_chat_stream``) is sync — wrapped via
    ``iterate_in_threadpool`` so the event loop isn't blocked while
    the model produces tokens. DB writes happen via
    ``run_in_threadpool`` for the same reason.
    """
    sess = await run_in_threadpool(
        sessions_repo.get,
        req.session_id,
        user.id,
    )
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")

    # Persist the user turn before streaming so a failed LLM call keeps it
    # on the timeline. Retries re-send the same text, so an identical user
    # row at the tail (no assistant after it) is reused, not duplicated.
    prior_count = await run_in_threadpool(
        sessions_repo.count_messages,
        req.session_id,
    )
    last = await run_in_threadpool(sessions_repo.last_message, req.session_id)
    is_retry_of_tail = (
        last is not None and last["role"] == "user" and last["content"] == req.content
    )
    # Used after the stream finishes to decide whether to enqueue title
    # generation. A retried first turn still counts as the first.
    is_first_turn = prior_count == 0 or (is_retry_of_tail and prior_count == 1)

    if not is_retry_of_tail:
        await run_in_threadpool(
            lambda: sessions_repo.append_message(
                req.session_id,
                role="user",
                content=req.content,
                events=None,
            ),
        )

    # Hydrate prior history (now including the just-saved user turn).
    # Hidden seed messages (e.g. drafting-from-template kickoffs) flow
    # into the model context but stay out of the rendered transcript.
    history = await run_in_threadpool(
        lambda: sessions_repo.get_messages(req.session_id, include_hidden=True),
    )
    messages: list[dict[str, Any]] = messages_from_history(history)

    session_id = req.session_id
    user_id = user.id

    async def stream() -> AsyncIterator[bytes]:
        events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        try:
            with (
                trace_flow(
                    "chat.send_message",
                    chat_session_id=session_id,
                    user_id=user_id,
                    is_first_turn=is_first_turn,
                ),
                chat_agent_scope(),
            ):
                raw_settings = await run_in_threadpool(users_repo.get_settings, user_id)
                user_prefs = UserSettings.model_validate(raw_settings or {})
                _inject_turn_context(
                    messages, user, req.context_paths, user_prefs.work_role
                )
                gen = run_chat_stream(
                    messages,
                    model=user_prefs.chat_model,
                    provider=user_prefs.chat_provider,
                )
                # iterate_in_threadpool yields each item from the sync
                # generator on a worker thread, so token emission
                # doesn't block the event loop. The agent-name
                # ContextVar must be set in *this* task context
                # (chat_agent_scope above) — setting it inside the
                # generator would bind it in a per-next() copied
                # context that neither propagates to other next()
                # calls nor permits reset across them.
                async for ev in iterate_in_threadpool(gen):
                    if await request.is_disconnected():
                        # Abort without persisting: the session tail must stay
                        # on the user row so a retry of the same text reuses it
                        # instead of appending a duplicate.
                        return
                    events.append(ev)
                    if ev.get("type") == "text_delta":
                        text_parts.append(ev.get("text", ""))
                    yield _sse(ev)
        except LLMError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
            return
        except Exception:
            log.exception("chat stream failed")
            yield _sse(
                {
                    "type": "error",
                    "code": "unknown",
                    "message": "The chat agent hit an unexpected error. Check the server logs.",
                }
            )
            return

        # A stopped client's stream can complete without ever observing the
        # disconnect mid-loop, so re-check before persisting.
        if await request.is_disconnected():
            return

        try:
            # Tail check and insert share one locked transaction, so a
            # stopped request racing its own retry can never store a
            # second answer for the turn.
            saved = await run_in_threadpool(
                lambda: sessions_repo.append_assistant_if_user_tail(
                    session_id,
                    user_content=req.content,
                    content="".join(text_parts),
                    events=events,
                ),
            )
            if saved is None:
                return
            await run_in_threadpool(sessions_repo.touch, session_id)
            # Emit after persistence so the client can rate the turn. Replay
            # reconstructs this event from the saved message row.
            yield _sse({"type": "message_saved", "id": saved["id"]})
        except Exception:
            log.exception("failed to persist assistant turn session_id=%s", session_id)

        if is_first_turn:
            try:
                await run_in_threadpool(generate_chat_title, session_id)
            except Exception:
                # Title generation failures are non-fatal — log and move on.
                log.exception("failed to enqueue title task session_id=%s", session_id)

    headers = {
        # ``no-transform`` tells intermediaries not to recode the body —
        # in particular it makes Express's ``compression`` middleware
        # (used by the Next.js dev server) skip gzip. Without it, the
        # dev proxy gzips the SSE stream and the browser sees one big
        # decompressed chunk at the end instead of token-by-token
        # streaming. nginx in prod also honors ``X-Accel-Buffering: no``.
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )


def _compose_blank_seed_message() -> str:
    """Synthetic user turn used by the "Blank document" picker option.

    Generic — no template body, no system prompt. Hints at the wiki's
    ability to auto-fill described sections so the user knows the
    shape of useful first instructions to give the agent.
    """
    return "\n".join(
        [
            "I am creating a new blank doc.",
            "",
            "Reply in a very short response — just a couple of sentences total. "
            "Do not lecture or list options. Open with one short, welcoming line "
            'like "Great, what would you like to work on?" and briefly mention '
            "that I can describe topics or sections I care about (a project I "
            "want to track, recurring updates, a running list) and the wiki "
            "will fill those in and keep them updated over time. Keep the "
            "overall response short.",
        ]
    )


def _compose_drafting_seed_message(template: dict[str, Any]) -> str:
    """Synthetic user turn that primes the agent for drafting from a
    template. Sent hidden so the user only sees the agent's response."""
    body = template["body"]
    name = template.get("name") or "document"
    system_prompt = template.get("system_prompt")
    parts = [
        f"I am creating a new {name} doc from the following template:",
        "",
        "```",
        body,
        "```",
    ]
    if system_prompt:
        parts.extend(
            [
                "",
                "Template instructions:",
                "",
                "```",
                system_prompt,
                "```",
            ]
        )
    parts.extend(
        [
            "",
            "Reply in a very short response — just a couple of sentences total. "
            "Do not summarize, describe, or comment on the template itself. "
            f'Open with one short sentence like "Great, let\'s build out this {name} together" '
            "and then ask 1 or 2 specific guiding questions to help me start filling "
            "in the most important parts. The goal is to help me complete the doc with "
            "as little effort as possible.",
            "",
            "Also mention, in one line, that I can have future agents fill in any section "
            "later by leaving a short note in the doc itself describing what belongs there "
            '(for example, "This section auto-fills with new updates over time" or '
            '"Log every mention of this project here with context") — the wiki will pick '
            "that up. Keep the overall response short.",
        ]
    )
    return "\n".join(parts)


@router.post("/drafting/init")
async def drafting_init(
    req: DraftingInitRequest,
    request: Request,
    user: User = Depends(require_user),
) -> Response:
    """Bootstrap a hidden chat session for drafting a new doc.

    With a ``template_id``: seeds the session from that template's body
    and (optional) system prompt. Without one: seeds a generic blank
    "what do you want to work on" prime. Either way the very first SSE
    event is ``{"type": "session_created", "session_id": …}`` so the
    client can pin subsequent ``send_message`` calls to this id.
    """
    tmpl: dict[str, Any] | None = None
    if req.template_id is not None:
        tmpl = await run_in_threadpool(wiki_templates.get, req.template_id)
        if tmpl is None:
            raise HTTPException(status_code=404, detail="template not found")

    sess = await run_in_threadpool(
        lambda: sessions_repo.create(user.id, hidden=True),
    )
    session_id = sess["id"]
    user_id = user.id

    def _append(content: str, *, hidden: bool) -> None:
        sessions_repo.append_message(
            session_id,
            role="user",
            content=content,
            events=None,
            hidden=hidden,
        )

    seed_text = (
        _compose_drafting_seed_message(tmpl)
        if tmpl is not None
        else _compose_blank_seed_message()
    )
    await run_in_threadpool(lambda: _append(seed_text, hidden=True))

    history = await run_in_threadpool(
        lambda: sessions_repo.get_messages(session_id, include_hidden=True),
    )
    messages: list[dict[str, Any]] = messages_from_history(history)

    async def stream() -> AsyncIterator[bytes]:
        # Announce the session id up front so the client can switch over
        # before the first text_delta lands.
        yield _sse({"type": "session_created", "session_id": session_id})
        events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        try:
            with (
                trace_flow(
                    "chat.drafting_init",
                    chat_session_id=session_id,
                    user_id=user_id,
                    template_id=tmpl["id"] if tmpl is not None else None,
                ),
                chat_agent_scope(),
            ):
                gen = run_chat_stream(messages)
                async for ev in iterate_in_threadpool(gen):
                    if await request.is_disconnected():
                        # Abort without persisting a partial kickoff turn.
                        return
                    events.append(ev)
                    if ev.get("type") == "text_delta":
                        text_parts.append(ev.get("text", ""))
                    yield _sse(ev)
        except LLMError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
            return
        except Exception:
            log.exception("drafting init stream failed")
            yield _sse(
                {
                    "type": "error",
                    "code": "unknown",
                    "message": "The chat agent hit an unexpected error. Check the server logs.",
                }
            )
            return

        try:
            await run_in_threadpool(
                lambda: sessions_repo.append_message(
                    session_id,
                    role="assistant",
                    content="".join(text_parts),
                    events=events,
                ),
            )
            await run_in_threadpool(sessions_repo.touch, session_id)
        except Exception:
            log.exception(
                "failed to persist drafting kickoff turn session_id=%s",
                session_id,
            )

    headers = {
        # See ``send_message`` for why ``no-transform`` matters.
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )
