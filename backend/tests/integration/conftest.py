"""Integration-test fixtures.

These wire the pieces from the top-level ``conftest`` (per-test Postgres
schema, tmp wiki repo) into a Flask app + test client + scripted LLM,
so a flow test can be written like:

    def test_my_flow(integration):
        integration.signin("u@x.com")
        integration.llm.respond(text="ok")  # script the LLM seam
        integration.put_doc("guide.md", "# Guide\\n\\nbody")
        events = integration.events()
        ...

The ``integration`` fixture composes everything — typical tests should
prefer it over wiring fixtures by hand.

What's already provided by the parent ``tests/conftest.py``:

  * ``tmp_config`` — per-test Postgres schema; ``CONFIG`` patched.
  * ``tmp_db``     — same + ``init_db()`` (runs ``alembic upgrade head`` —
                     extensions, ``Base.metadata.create_all`` via the
                     bootstrap migration, and ``pgmq.create``) against
                     that schema.
  * ``tmp_repo``   — same + a freshly initialized wiki git repo.

What this file adds:

  * ``immediate_queues`` — every ``TaskQueue`` runs handlers inline, so
    background work (reindex, trigger fan-out, agent-activity cleanup)
    happens in the test thread. Default for integration tests.
  * ``mock_llm``        — patches ``app.llm.client.complete`` and ``stream``
    with a scripted responder. Tests register canned answers and assert on
    captured calls.
  * ``app`` / ``client`` — a real FastAPI app via ``app.main.create_app``
    (the test factory builds the app without the lifespan firing — the
    schema is already migrated by ``tmp_db``).
  * ``integration``     — composite fixture that bundles the above plus a
    handful of high-level helpers (signup, login, PUT doc, list events).

LLM mocking: patch at the seam (``app.llm.client``), not the SDK. The
``MockLLM`` object captures every call and lets tests script responses
keyed by predicate; default is a benign empty answer so a test that
doesn't care doesn't blow up the trigger evaluator on its way through.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.llm.client import CompletionResult
from app.tasks.queues import QUEUES

from tests._auth import login_fastapi


# --------------------------------------------------------------------------- #
# Background-work isolation                                                   #
# --------------------------------------------------------------------------- #


@pytest.fixture
def immediate_queues() -> Iterator[dict[str, Any]]:
    """Run every task queue in immediate mode (handlers execute inline).

    Background work fans out exactly like in production, but synchronously,
    so test assertions can run on the post-state without polling. If a
    test wants to exercise the real pgmq path it should not request this
    fixture (and should be aware that pgmq tables are database-scoped, so
    cross-test message leakage is possible).
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        for q in QUEUES.values():
            stack.enter_context(q.immediate_mode())
        yield QUEUES


# --------------------------------------------------------------------------- #
# LLM mock                                                                    #
# --------------------------------------------------------------------------- #


def _make_default_response() -> CompletionResult:
    return CompletionResult(text="", tool_calls=[], stop_reason="end_turn")


class MockLLM:
    """Scripted replacement for ``app.llm.client.complete`` / ``stream``.

    Register a canned response with ``respond(...)``; the next call
    matching ``when`` returns it. Calls without a matching script return
    a benign empty response and are recorded for inspection.

    The same mock backs both ``complete`` (returns a fresh
    ``CompletionResult``) and ``stream`` (yields one ``done`` event
    built from the same payload). The seam shape is the pydantic
    ``CompletionResult`` from ``app.llm.client`` — match production.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []   # captured call kwargs
        # Each script is (predicate, builder) — builder returns a fresh
        # CompletionResult so callers can't mutate cached state.
        self._scripts: list[tuple[Callable[[dict[str, Any]], bool], Callable[[], Any]]] = []

    # ---- scripting ---------------------------------------------------------

    def respond(
        self,
        *,
        when: Callable[[dict[str, Any]], bool] | None = None,
        text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str = "end_turn",
    ) -> None:
        """Register a response. ``when(call)`` filters by call kwargs;
        the default (``None``) matches every call.
        """
        tcs = list(tool_calls or [])

        def build() -> Any:
            from app.llm.client import CompletionResult, ToolCall
            return CompletionResult(
                text=text,
                tool_calls=[ToolCall(**tc) for tc in tcs],
                stop_reason=stop_reason,
            )

        self._scripts.append((when or (lambda _c: True), build))

    def raise_for(
        self,
        exc: BaseException,
        *,
        when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        """Make matching calls raise ``exc`` instead of returning a result.

        Useful for the ``LLMError`` swallow paths — the mock can simulate
        the provider going down without the test needing to patch the
        seam itself.
        """
        def build() -> Any:
            raise exc

        self._scripts.append((when or (lambda _c: True), build))

    def respond_match(self, pattern: str, text: str = "") -> None:
        """Convenience: respond with ``text`` to any call whose serialized
        messages contain ``pattern`` (regex). Useful for keying responses
        off prompt content without writing a full predicate.
        """
        rx = re.compile(pattern)
        def matches(call: dict[str, Any]) -> bool:
            return bool(rx.search(json.dumps(call.get("messages", []))))
        self.respond(when=matches, text=text)

    def reset(self) -> None:
        self.calls.clear()
        self._scripts.clear()

    # ---- patched seam ------------------------------------------------------

    def complete(self, messages, *, tools=None, max_tokens=None, model=None, provider=None) -> Any:
        call = {"messages": messages, "tools": tools, "max_tokens": max_tokens, "model": model}
        self.calls.append(call)
        for matcher, build in self._scripts:
            if matcher(call):
                return build()
        return _make_default_response()

    def stream(self, messages, *, tools=None, max_tokens=None, model=None, provider=None) -> Iterator[dict]:
        resp = self.complete(messages, tools=tools, max_tokens=max_tokens, model=model)
        if resp.text:
            yield {"type": "text_delta", "text": resp.text}
        for tc in resp.tool_calls:
            yield {"type": "tool_call", "id": tc.id, "name": tc.name,
                   "arguments": tc.arguments}
        yield {"type": "done", "stop_reason": resp.stop_reason,
               "usage": resp.usage.model_dump()}


@pytest.fixture
def mock_llm(monkeypatch) -> MockLLM:
    """Patch ``app.llm.client.complete``/``stream`` with a scripted mock.

    Returns the ``MockLLM`` so tests can register canned answers and
    inspect ``mock.calls`` after the action runs.
    """
    m = MockLLM()
    monkeypatch.setattr("app.llm.client.complete", m.complete)
    monkeypatch.setattr("app.llm.client.stream", m.stream)
    # The trigger NL module imports complete directly at module load —
    # rebind that captured reference too.
    monkeypatch.setattr("app.triggers.natural_language.complete", m.complete)
    return m


# --------------------------------------------------------------------------- #
# FastAPI app + client                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def app(tmp_repo) -> FastAPI:
    """Real ``create_app`` against the per-test schema + wiki repo."""
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Composite fixture                                                           #
# --------------------------------------------------------------------------- #


class IntegrationHarness:
    """Bundle of a FastAPI client + LLM mock + assertion helpers.

    Don't add domain logic here that doesn't have a counterpart in the
    real app — the harness exists to exercise the same code paths a
    request would, not to fake out half the system.
    """

    def __init__(self, client: TestClient, llm: MockLLM, queues: dict[str, Any]) -> None:
        self.client = client
        self.llm = llm
        self.queues = queues

    # ----- auth -------------------------------------------------------------

    def signup(self, email: str = "u@x.com", password: str = "hunter22", name: str | None = "U") -> str:
        """POST /api/auth/signup. Returns the new user_id."""
        resp = self.client.post(
            "/api/auth/signup",
            json={"email": email, "password": password, "name": name},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def signin(self, user_id: str | None = None, *, email: str | None = None) -> str:
        """Set a session for the given user. Either ``user_id`` directly,
        or look up by ``email``. Returns the user_id used.
        """
        resolved_id: str
        if user_id is None:
            from app.auth import users as users_repo
            assert email is not None, "pass user_id or email"
            row = users_repo.get_by_email(email)
            assert row is not None, f"no user for {email}"
            resolved_id = row["id"]
        else:
            resolved_id = user_id
        login_fastapi(self.client, resolved_id)
        return resolved_id

    def signup_and_signin(self, email: str = "u@x.com", password: str = "hunter22") -> str:
        uid = self.signup(email=email, password=password)
        # signup already creates a session, but be explicit so the harness
        # behaves the same in tests that bypass /signup.
        self.signin(uid)
        return uid

    # ----- documents --------------------------------------------------------

    def put_doc(self, path: str, body: str) -> dict:
        resp = self.client.put("/api/documents/file", json={"path": path, "body": body})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()

    def delete_doc(self, path: str) -> None:
        resp = self.client.delete(f"/api/documents/file?path={path}")
        assert resp.status_code == 200, resp.text

    # ----- triggers ---------------------------------------------------------

    def create_trigger(self, *, scope_path: str, condition: str, message: str) -> str:
        """POST /api/triggers; return the new trigger id."""
        resp = self.client.post(
            "/api/triggers",
            json={"scope_path": scope_path, "nl_description": condition, "message": message},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    # ----- events / state ---------------------------------------------------

    def events(self, *, kind: str | None = None, limit: int = 100) -> list[dict]:
        """GET /api/events (newest first), optionally filtered by ``kind``."""
        path = f"/api/events?limit={limit}"
        if kind:
            path += f"&kind={kind}"
        resp = self.client.get(path)
        assert resp.status_code == 200, resp.text
        return resp.json()["events"]

    def fired_triggers(self) -> list[dict]:
        return self.events(kind="trigger.fire")


@pytest.fixture
def integration(client, mock_llm, immediate_queues) -> IntegrationHarness:
    """One-stop fixture for integration tests.

    Pulls in the Flask client (with real DB + wiki repo behind it),
    the scripted LLM mock, and immediate-mode queues. Exposes a thin
    set of helpers (signup, signin, put_doc, create_trigger, events)
    that mirror real API calls — no in-process shortcuts that bypass
    the seams under test.
    """
    return IntegrationHarness(client=client, llm=mock_llm, queues=immediate_queues)
