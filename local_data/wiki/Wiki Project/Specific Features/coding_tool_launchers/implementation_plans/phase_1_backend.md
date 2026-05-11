# Phase 1 — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the backend half of coding-tool-launchers behind a feature flag so a future helper PR (Phase 3) and frontend PR (Phase 2) can plug into a stable, tested API surface. No frontend changes, no npm helper, no real CLI integration. Everything is exercised via FastAPI `TestClient` against a real per-test Postgres schema.

**Architecture:** Approach C (hybrid manifest registry) hardened by helper-side binary allow-list. New tables `launch_codes`, `agent_sessions`, `page_working_dirs`; extension column on `agent_activity`; manifest registry under `app/launchers/manifests/` (validated by pydantic + DSL rules); new routers `app/api/launchers.py` and `app/api/agent_sessions.py`; new task `expire_launch_artifacts` on `lightweight_maintenance_queue`; `X-Agentwiki-Session` header threading on the MCP server with cross-user 403.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 ORM (`Mapped[T]` + `mapped_column`), Alembic migrations, pydantic v2 `BaseModel`, pytest + `TestClient`, pgmq via `app/tasks/queue.py`. All conventions per `/Users/nikolas/agent-wiki/CLAUDE.md`.

**Reference:** [../design.md](../design.md) — read sections "Data model", "Launch protocol", "Manifest DSL", "Security model" before starting. Resolved P1 items #1–#6 and P2 #7 are all in scope of this plan.

---

## Audit fixes — apply during task execution

A self-audit after the plan was written found 8 issues in Phase 1's task bodies. **Apply these inline as you execute each named task.** They are listed here in one place so the executing agent picks them up regardless of which task they start with.

### AF#1 — ACL check on `POST /api/launch` (audit critical)

**Affects: Task 16.**

In `post_launch`, **before** reading the page body or parsing frontmatter, gate by ACL:

```python
from app.wiki import acl as wiki_acl

if req.wiki_path is not None:
    if not wiki_acl.can("read", req.wiki_path, user):
        raise HTTPException(status_code=403, detail="forbidden")
```

(Use whichever helper exists — `app.auth.require_can(...)` or `app.wiki.acl.can(...)`; verify by grep on Task 16 prep.)

Add an `test_post_launch_forbidden_on_unreadable_page` test to `test_launch_api.py` — seed a doc, deny read for the user via ACL, POST launch → expect 403.

### AF#2 — Heartbeat / cli-session / close routes accept bearer OR cookie (audit critical)

**Affects: Task 19.**

The helper has only an MCP bearer; current plan's `Depends(require_user)` requires cookie. Replace with a new `require_user_or_bearer` dependency:

```python
# In app/auth/deps.py — new dependency
from fastapi import Request

def require_user_or_bearer(request: Request) -> User:
    # Try cookie first (works in tests + browser-driven calls).
    user = _maybe_user_from_cookie(request)
    if user is not None:
        return user
    # Fall back to MCP bearer.
    return require_bearer(request)
```

Update Task 19's router to use `Depends(require_user_or_bearer)` on `/heartbeat`, `/cli-session`, `/close`. Add tests:

- `test_heartbeat_with_bearer` — helper-style call with `Authorization: Bearer mcp_…` succeeds (204).
- `test_heartbeat_with_foreign_bearer_403` — bearer for user B against session of user A returns 403 (already covered by `_require_own_session` after the dep resolves).

### AF#3 — `launcher_tokens.get_or_mint_for_user` race fix (audit critical)

**Affects: Task 17.4.**

Current plan: two `with session()` blocks separated by `tokens_repo.create()`. Race: two concurrent launches both see "no token" → both mint.

Patch: add a unique constraint to `launcher_tokens.user_id` so only one row per user exists. Migration change (Task 6):

```python
# In 0014_launchers.py upgrade — replace the launcher_tokens block:
op.create_table(
    "launcher_tokens",
    sa.Column(
        "mcp_token_id",
        sa.Text(),
        sa.ForeignKey("mcp_tokens.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # NEW: unique constraint on user_id so concurrent mints collide.
    sa.Column(
        "user_id",
        sa.Text(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
    sa.Column("nonce", sa.LargeBinary(), nullable=False),
    sa.Column("created_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
)
```

And the LauncherToken model gains `user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), unique=True, nullable=False)`.

Patch `get_or_mint_for_user` to use `pg_insert(...).on_conflict_do_nothing(index_elements=["user_id"])` semantics:

```python
def get_or_mint_for_user(user_id: str, *, name: str) -> tuple[str, str]:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    # First, optimistic SELECT.
    with session() as s:
        row = s.scalar(select(LauncherToken).where(LauncherToken.user_id == user_id))
        if row is not None:
            try:
                raw = AESGCM(_key()).decrypt(row.nonce, row.ciphertext, None).decode("utf-8")
                return row.mcp_token_id, raw
            except Exception:
                log.warning("launcher_token decrypt failed for user=%s; re-minting", user_id)
                s.delete(row)  # delete stale ciphertext (audit fix #15)

    # Mint fresh — guarded by unique constraint, so a concurrent racer hits IntegrityError.
    token_id, raw = tokens_repo.create(user_id, name)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, raw.encode("utf-8"), None)
    with session() as s:
        stmt = pg_insert(LauncherToken).values(
            mcp_token_id=token_id, user_id=user_id, ciphertext=ciphertext, nonce=nonce,
        ).on_conflict_do_nothing(index_elements=["user_id"])
        result = s.execute(stmt)
        if result.rowcount == 0:
            # Lost the race; the OTHER request inserted. Roll back our mcp_token row
            # (it's now orphaned) and re-fetch theirs.
            tokens_repo.revoke(token_id, user_id)
            row = s.scalar(select(LauncherToken).where(LauncherToken.user_id == user_id))
            assert row is not None
            raw = AESGCM(_key()).decrypt(row.nonce, row.ciphertext, None).decode("utf-8")
            return row.mcp_token_id, raw
    return token_id, raw
```

Add `test_launcher_tokens_concurrent_mint_idempotent` to a new `test_launcher_tokens.py` — exercises the conflict path.

### AF#5 — Validator rejects `${prompt_file_path}` in resume (audit critical)

**Affects: Task 10.2.**

In `registry.py`'s `LaunchBlock._validate_vars`, when the block is a `ResumeBlock`, also reject `${prompt_file_path}` anywhere. The cleanest way: pass a `block_kind` literal through (`"launch"` | `"resume"`) and gate the check:

```python
class ResumeBlock(LaunchBlock):
    @model_validator(mode="after")
    def _validate_resume_specific(self) -> "ResumeBlock":
        for i, a in enumerate(self.argv):
            if "${first_turn_prompt}" in a or "${prompt_file_path}" in a:
                raise ValueError(
                    f"${{first_turn_prompt}} / ${{prompt_file_path}} forbidden in "
                    f"resume.argv (first-turn-only). Offending argv[{i}]={a!r}."
                )
        for k, v in self.env.items():
            if "${first_turn_prompt}" in v or "${prompt_file_path}" in v:
                raise ValueError(f"forbidden in resume.env.{k}")
        if self.cwd and ("${first_turn_prompt}" in self.cwd or "${prompt_file_path}" in self.cwd):
            raise ValueError("forbidden in resume.cwd")
        return self
```

Add the test `test_prompt_file_path_in_resume_rejected` to `test_launchers_registry.py`.

### AF#10 — Catalog filter on `available_for_launch` (audit high)

**Affects: Task 15.1 (LauncherCatalogEntry) + Task 16.2 (post_launch).**

Add `available_for_launch: bool` to `LauncherCatalogEntry` (Task 14). Compute in `get_catalog`:

```python
def _entry_available(m: Manifest) -> bool:
    if m.kind == "local_cli":
        return True  # backend has the launch path
    if m.kind == "in_app":
        return False  # onyx-craft has no shipped POST /api/craft/launch yet
    if m.kind == "web_handoff":
        return False
    return False
```

Frontend (Phase 2) hides tools where `available_for_launch == False` from the Run-radio. They still render on `/agents` Coding tools section as "configure-only" / "coming soon".

Backend already 400s on in_app via Task 16 — keep that as defense-in-depth.

### AF#11 — `status=failed` mapping on close (audit high)

**Affects: Task 19.4 close route.**

Current plan always sets `status=closed`. Patch:

```python
_ERROR_REASONS = frozenset({
    "cli_not_found",
    "invalid_workdir",
    "spawn_failed",
    "binary_not_allowed",
    "manifest_version_unsupported",
})

@router.post("/{sid}/close", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    sid: str, req: CloseRequest, user: User = Depends(require_user_or_bearer),
) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    reason = req.reason or "user_clicked"
    if reason in _ERROR_REASONS:
        sessions_repo.mark_failed(sid, reason=reason)
    else:
        sessions_repo.close(sid, reason=reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Add tests:

- `test_close_with_error_reason_marks_failed` — POST close with `reason="cli_not_found"` → `status=failed`.
- `test_close_with_user_reason_marks_closed` — POST close with `reason="user_clicked"` → `status=closed`.

### AF#12 — Drop `AGENTWIKI_MCP_TOKEN` from env in shipped manifests (audit high)

**Affects: Task 11.2 (claude_code.json) + Task 11.3 (codex.json).**

In both shipped manifests, remove the `AGENTWIKI_MCP_TOKEN` env entry. Token lives only in the `mcp_config_path` tmpfile (`claude_json` / `codex_toml` adapters already write it there). Env keeps `AGENTWIKI_SESSION_ID` + `AGENTWIKI_ENDPOINT`.

Update `test_claude_manifest_passes_token_argv_rule` (Task 11.1) to additionally assert `"${token}" not in (manifest.launch.env or {}).values()`.

The validator itself stays permissive (other tools may need env-side token by explicit declaration); shipped v1 manifests are the policy enforcement.

### AF#15 — Graceful AES decrypt failure (audit medium)

**Affects: Task 17.4 (already covered in AF#3 above — re-mint on decrypt failure rather than 500).**

The patched `get_or_mint_for_user` in AF#3 already handles this. No additional fix needed; AF#3 references this.

---

## Pre-flight

- [ ] **Step 0.1: Confirm dev env**

```bash
cd /Users/nikolas/agent-wiki/backend
uv sync --extra dev
```

Confirm `psql -h localhost -U postgres -c '\l' | grep agent_wiki_test` shows the test DB exists. If not:

```bash
createdb agent_wiki_test
psql agent_wiki_test -c 'CREATE EXTENSION pg_textsearch;'
psql agent_wiki_test -c 'CREATE EXTENSION pgmq CASCADE;'
```

- [ ] **Step 0.2: Confirm branch + clean tree**

```bash
git -C /Users/nikolas/agent-wiki status
git -C /Users/nikolas/agent-wiki branch --show-current
```

Expected: branch is `feat/coding-tool-launchers` (or a sub-branch off it for the implementation work), working tree clean.

- [ ] **Step 0.3: Confirm pre-commit installed**

```bash
cd /Users/nikolas/agent-wiki && pre-commit install
```

---

## File Structure

New backend files this plan creates:

```
backend/app/
  config.py                                            (modify — add launchers_enabled flag)
  db/
    models.py                                          (modify — add 3 models + column)
    migrations/versions/
      0014_launchers.py                                (create)
  launchers/
    __init__.py                                        (create)
    registry.py                                        (create — Manifest model + validator + loader)
    prompt_builder.py                                  (create — first_turn_prompt composer)
    sessions.py                                        (create — agent_sessions repo)
    page_dirs.py                                       (create — page_working_dirs repo)
    manifests/
      claude_code.json                                 (create)
      codex.json                                       (create)
      onyx_craft.json                                  (create)
  auth/
    launch_codes.py                                    (create — launch_codes repo)
  models/
    launchers.py                                       (create — HTTP request/response shapes)
  api/
    launchers.py                                       (create — 4 routes)
    agent_sessions.py                                  (create — 4 routes)
    mcp_server.py                                      (modify — read X-Agentwiki-Session, 403 on cross-user)
  wiki/
    linked_repos.py                                    (create — frontmatter `linked_repos` parser)
    agent_activity.py                                  (modify — accept agent_session_id on upsert)
  tasks/
    expire_launch_artifacts.py                         (create)
    run_worker.py                                      (modify — import new task module)
  main.py                                              (modify — register routers, gate by flag)
backend/tests/
  test_launch_codes.py                                 (create)
  test_launchers_manifests.py                          (create)
  test_launchers_registry.py                           (create)
  test_launchers_prompt_builder.py                     (create)
  test_launchers_sessions.py                           (create)
  test_launchers_page_dirs.py                          (create)
  test_linked_repos.py                                 (create)
  test_launch_api.py                                   (create)
  test_agent_sessions_api.py                           (create)
  test_mcp_session_stamp.py                            (create)
  test_expire_launch_artifacts.py                      (create)
  integration/
    test_launch_e2e.py                                 (create)
```

Each file has a single, well-scoped responsibility. The registry, prompt builder, repos, and routers are all small modules (<200 LOC each).

---

## Task 1: Feature flag

**Files:**

- Modify: `backend/app/config.py`
- Test: covered indirectly by Task 16 + Task 17 (API gating tests)

- [ ] **Step 1.1: Add `launchers_enabled` to `Config`**

Open `backend/app/config.py`. Add to the `Config` class:

```python
class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ... existing fields ...

    launchers_enabled: bool
```

Find the `load_config()` function (search for `def load_config`). Add to its return value:

```python
def load_config() -> Config:
    return Config(
        # ... existing kwargs ...
        launchers_enabled=os.environ.get("LAUNCHERS_ENABLED", "false").lower() == "true",
    )
```

- [ ] **Step 1.2: Update test conftest to set the flag**

Open `backend/tests/conftest.py`. Find the `Config(...)` instantiation in the `tmp_config` fixture. Add:

```python
cfg = Config(
    # ... existing kwargs ...
    launchers_enabled=True,
)
```

Tests run with the flag ON so they can exercise the API surface. Prod default is OFF.

- [ ] **Step 1.3: Run existing tests to confirm nothing broke**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_health_api.py -v
```

Expected: PASS.

- [ ] **Step 1.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/config.py backend/tests/conftest.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): add LAUNCHERS_ENABLED config flag (default off)"
```

---

## Task 2: `AgentSession` model

**Files:**

- Modify: `backend/app/db/models.py:111` (insert after `McpJob`)
- Test: `backend/tests/test_launchers_sessions.py` (created later in Task 8)

- [ ] **Step 2.1: Add `AgentSession` model class**

Open `backend/app/db/models.py`. After the `McpJob` class (which ends around line 156), insert:

```python
class AgentSession(Base):
    """One Run-Agent invocation. Tracks the launch through CLI lifetime.

    Lives across the URI handshake, the exchange, the spawned CLI's
    activity, and final close. ``machine_id`` identifies the user's
    laptop/desktop (helper persists a uuid per install); ``cli_session_id``
    is whatever the spawned tool (claude/codex) called its conversation.
    See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
    """

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    machine_id: Mapped[str | None] = mapped_column(Text)
    tool_id: Mapped[str] = mapped_column(Text, nullable=False)
    wiki_path: Mapped[str | None] = mapped_column(Text)
    working_dir: Mapped[str | None] = mapped_column(Text)
    first_turn_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cli_session_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    started_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=_NOW_TEXT_DEFAULT)
    last_activity_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=_NOW_TEXT_DEFAULT)
    closed_at: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_agent_sessions_user_status", "user_id", "status"),
        Index("idx_agent_sessions_wiki_path", "wiki_path"),
        Index("idx_agent_sessions_user_machine", "user_id", "machine_id"),
    )
```

- [ ] **Step 2.2: Verify import roundtrip**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "from app.db.models import AgentSession; print(AgentSession.__tablename__)"
```

Expected: `agent_sessions`

- [ ] **Step 2.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/models.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): add AgentSession model"
```

---

## Task 3: `LaunchCode` model

**Files:**

- Modify: `backend/app/db/models.py` (insert after `AgentSession`)

- [ ] **Step 3.1: Add `LaunchCode` class**

Open `backend/app/db/models.py`. Immediately after the `AgentSession` class added in Task 2, insert:

```python
class LaunchCode(Base):
    """Short-lived single-use bearer the helper exchanges for the MCP token + manifest payload.

    Lives for 60 seconds (per spec — bump to 180s pending P2 #10). On
    successful exchange ``consumed_at`` is stamped and the row sticks
    around until the next sweep so a second exchange attempt can return
    409 instead of "not found".
    """

    __tablename__ = "launch_codes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agent_session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    mcp_token_id: Mapped[str] = mapped_column(
        Text, ForeignKey("mcp_tokens.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=_NOW_TEXT_DEFAULT)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_launch_codes_expires_at", "expires_at"),
    )
```

- [ ] **Step 3.2: Verify**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "from app.db.models import LaunchCode; print(LaunchCode.__tablename__)"
```

Expected: `launch_codes`

- [ ] **Step 3.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/models.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): add LaunchCode model"
```

---

## Task 4: `PageWorkingDir` model

**Files:**

- Modify: `backend/app/db/models.py` (insert after `LaunchCode`)

- [ ] **Step 4.1: Add `PageWorkingDir` class**

Open `backend/app/db/models.py`. Immediately after `LaunchCode`, insert:

```python
class PageWorkingDir(Base):
    """Per-(user, machine, page) working directory binding.

    Machine in the PK because the same user has different local checkout
    paths on different laptops. Helper sends ``machine_id`` on exchange;
    the launch endpoint writes this row when the user ticks "remember as
    default for this page" in the wizard.
    """

    __tablename__ = "page_working_dirs"

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    machine_id: Mapped[str] = mapped_column(Text, primary_key=True)
    wiki_path: Mapped[str] = mapped_column(Text, primary_key=True)
    working_dir: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_NOW_TEXT_DEFAULT,
    )
```

- [ ] **Step 4.2: Verify**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "from app.db.models import PageWorkingDir; print(PageWorkingDir.__tablename__)"
```

Expected: `page_working_dirs`

- [ ] **Step 4.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/models.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): add PageWorkingDir model"
```

---

## Task 5: `agent_activity.agent_session_id` column

**Files:**

- Modify: `backend/app/db/models.py:406` (the `AgentActivity` class)

- [ ] **Step 5.1: Add nullable FK column**

Open `backend/app/db/models.py`. Find the `AgentActivity` class. After the `cleanup_msg_id` field, insert:

```python
    # Set by the MCP server when the request carries
    # ``X-Agentwiki-Session: as_…``. NULL for chat-agent edits and any
    # MCP call without the header. See coding_tool_launchers/design.md.
    agent_session_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("agent_sessions.id", ondelete="SET NULL")
    )
```

And add to `__table_args__`:

```python
        Index("idx_agent_activity_session", "agent_session_id"),
```

- [ ] **Step 5.2: Verify**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "from app.db.models import AgentActivity; assert hasattr(AgentActivity, 'agent_session_id'); print('ok')"
```

Expected: `ok`

- [ ] **Step 5.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/models.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): add agent_activity.agent_session_id column"
```

---

## Task 6: Alembic migration `0014_launchers`

**Files:**

- Create: `backend/app/db/migrations/versions/0014_launchers.py`

- [ ] **Step 6.1: Write the migration manually (do NOT use autogenerate)**

Migrations in this codebase are hand-written for clarity. Create `backend/app/db/migrations/versions/0014_launchers.py`:

```python
"""launch_codes + agent_sessions + page_working_dirs + agent_activity.agent_session_id

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-11

Coding-tool-launchers Phase 1. Three new tables + one nullable FK
column on agent_activity. ``op.create_table`` is guarded by
``has_table`` because ``0001_initial`` runs ``Base.metadata.create_all``
and will materialize these tables on fresh DBs authored after this
revision shipped (same pattern as ``0004_mcp_jobs.py``).
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text(
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("agent_sessions"):
        op.create_table(
            "agent_sessions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("machine_id", sa.Text()),
            sa.Column("tool_id", sa.Text(), nullable=False),
            sa.Column("wiki_path", sa.Text()),
            sa.Column("working_dir", sa.Text()),
            sa.Column("first_turn_prompt", sa.Text(), nullable=False),
            sa.Column("cli_session_id", sa.Text()),
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("started_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
            sa.Column("last_activity_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
            sa.Column("closed_at", sa.Text()),
        )
        op.create_index("idx_agent_sessions_user_status", "agent_sessions", ["user_id", "status"])
        op.create_index("idx_agent_sessions_wiki_path", "agent_sessions", ["wiki_path"])
        op.create_index("idx_agent_sessions_user_machine", "agent_sessions", ["user_id", "machine_id"])

    if not inspector.has_table("launch_codes"):
        op.create_table(
            "launch_codes",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "agent_session_id",
                sa.Text(),
                sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "mcp_token_id",
                sa.Text(),
                sa.ForeignKey("mcp_tokens.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("consumed_at", sa.Text()),
        )
        op.create_index("idx_launch_codes_expires_at", "launch_codes", ["expires_at"])

    if not inspector.has_table("page_working_dirs"):
        op.create_table(
            "page_working_dirs",
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("machine_id", sa.Text(), primary_key=True),
            sa.Column("wiki_path", sa.Text(), primary_key=True),
            sa.Column("working_dir", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
        )

    # agent_activity.agent_session_id — additive nullable column.
    cols = {c["name"] for c in inspector.get_columns("agent_activity")}
    if "agent_session_id" not in cols:
        op.add_column(
            "agent_activity",
            sa.Column(
                "agent_session_id",
                sa.Text(),
                sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index("idx_agent_activity_session", "agent_activity", ["agent_session_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_activity_session", table_name="agent_activity")
    op.drop_column("agent_activity", "agent_session_id")
    op.drop_table("page_working_dirs")
    op.drop_index("idx_launch_codes_expires_at", table_name="launch_codes")
    op.drop_table("launch_codes")
    op.drop_index("idx_agent_sessions_user_machine", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_wiki_path", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_user_status", table_name="agent_sessions")
    op.drop_table("agent_sessions")
```

- [ ] **Step 6.2: Apply migration to a scratch schema to verify it runs**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "
from app.db.session import init_db
init_db()
print('migration applied')
"
```

Expected: `migration applied` with no errors. (Note: `init_db()` against the default `DATABASE_URL` may already have run 0014 once; running again is a no-op thanks to the `has_table` guards.)

- [ ] **Step 6.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/migrations/versions/0014_launchers.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): migration 0014 — launch_codes, agent_sessions, page_working_dirs, agent_activity.agent_session_id"
```

---

## Task 7: `launch_codes` repo

**Files:**

- Create: `backend/app/auth/launch_codes.py`
- Test: `backend/tests/test_launch_codes.py`

- [ ] **Step 7.1: Write the failing test**

Create `backend/tests/test_launch_codes.py`:

```python
"""Repo round-trip for short-lived launch codes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db, session
from app.db.models import AgentSession

from tests._seed import seed_user


def _seed_session(uid: str, sid: str = "as_1") -> str:
    with session() as s:
        s.add(
            AgentSession(
                id=sid, user_id=uid, tool_id="claude-code",
                first_turn_prompt="hello",
            )
        )
    return sid


def test_create_roundtrip(tmp_config):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")

    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)

    assert raw.startswith("lc_")
    consumed = codes_repo.consume(raw)
    assert consumed is not None
    assert consumed["user_id"] == uid
    assert consumed["agent_session_id"] == sid
    assert consumed["mcp_token_id"] == tid


def test_consume_idempotent_second_call_returns_consumed_marker(tmp_config):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    assert codes_repo.consume(raw) is not None
    assert codes_repo.consume(raw) == "already_consumed"


def test_consume_expired_returns_expired(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    # Mint with TTL=0 by monkeypatching the constant for this test.
    monkeypatch.setattr(codes_repo, "_TTL_SECONDS", 0)
    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    assert codes_repo.consume(raw) == "expired"


def test_consume_unknown_returns_none(tmp_config):
    init_db()
    assert codes_repo.consume("lc_does_not_exist") is None


def test_expire_sweep_deletes_old_codes(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    monkeypatch.setattr(codes_repo, "_TTL_SECONDS", 0)
    codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    deleted = codes_repo.expire_sweep()
    assert deleted == 1
    assert codes_repo.expire_sweep() == 0
```

Run it:

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_codes.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.launch_codes'` (or similar).

- [ ] **Step 7.2: Write the repo**

Create `backend/app/auth/launch_codes.py`:

```python
"""Repo for ``launch_codes`` — single-use short-lived bearers the helper exchanges for the real MCP token.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import delete, select

from app.db.models import LaunchCode
from app.db.session import session

log = logging.getLogger(__name__)

_TOKEN_PREFIX = "lc_"
_TOKEN_BYTES = 32
_TTL_SECONDS = 60


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create(*, user_id: str, agent_session_id: str, mcp_token_id: str) -> str:
    """Mint a fresh launch code. Returns the raw value.

    Caller is responsible for placing it in the ``agentwiki://`` URI the
    frontend hands to the OS.
    """
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    expires_at = _iso(now + timedelta(seconds=_TTL_SECONDS))
    with session() as s:
        s.add(
            LaunchCode(
                id=raw,
                user_id=user_id,
                agent_session_id=agent_session_id,
                mcp_token_id=mcp_token_id,
                expires_at=expires_at,
            )
        )
    log.info(
        "launch_code minted user=%s session=%s expires=%s",
        user_id, agent_session_id, expires_at,
    )
    return raw


def consume(raw: str) -> dict[str, Any] | Literal["already_consumed", "expired"] | None:
    """Atomically claim a code.

    Returns:
        * ``dict`` with ``{user_id, agent_session_id, mcp_token_id}`` on success.
        * ``"already_consumed"`` if the code was already exchanged.
        * ``"expired"`` if past ``expires_at``.
        * ``None`` if the code doesn't exist.
    """
    if not raw.startswith(_TOKEN_PREFIX):
        return None
    now = datetime.now(timezone.utc)
    now_iso = _iso(now)
    with session() as s:
        row = s.get(LaunchCode, raw)
        if row is None:
            return None
        if row.consumed_at is not None:
            return "already_consumed"
        if row.expires_at < now_iso:
            return "expired"
        row.consumed_at = now_iso
        return {
            "user_id": row.user_id,
            "agent_session_id": row.agent_session_id,
            "mcp_token_id": row.mcp_token_id,
        }


def expire_sweep() -> int:
    """Delete codes past ``expires_at``. Returns count deleted."""
    now_iso = _iso(datetime.now(timezone.utc))
    with session() as s:
        result = s.execute(
            delete(LaunchCode).where(LaunchCode.expires_at < now_iso)
        )
        return int(result.rowcount or 0)
```

- [ ] **Step 7.3: Run test to verify it passes**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_codes.py -v
```

Expected: 5 passed.

- [ ] **Step 7.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/auth/launch_codes.py backend/tests/test_launch_codes.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): launch_codes repo + tests"
```

---

## Task 8: `agent_sessions` repo

**Files:**

- Create: `backend/app/launchers/__init__.py` (empty package marker)
- Create: `backend/app/launchers/sessions.py`
- Test: `backend/tests/test_launchers_sessions.py`

- [ ] **Step 8.1: Create the launchers package**

```bash
mkdir -p /Users/nikolas/agent-wiki/backend/app/launchers
touch /Users/nikolas/agent-wiki/backend/app/launchers/__init__.py
```

- [ ] **Step 8.2: Write the failing test**

Create `backend/tests/test_launchers_sessions.py`:

```python
"""Repo round-trip for agent_sessions."""
from __future__ import annotations

import pytest

from app.db.session import init_db
from app.launchers import sessions as sessions_repo
from tests._seed import seed_user


def test_create_minimal(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="hello",
        wiki_path="docs/x.md",
        working_dir="/tmp/work",
    )
    assert sid.startswith("as_")
    row = sessions_repo.get(sid)
    assert row is not None
    assert row["user_id"] == uid
    assert row["status"] == "pending"
    assert row["machine_id"] is None  # not yet set


def test_set_active_and_machine_id(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m_abc")
    row = sessions_repo.get(sid)
    assert row["status"] == "active"
    assert row["machine_id"] == "m_abc"


def test_set_cli_session_id(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.set_cli_session_id(sid, "cli_xyz")
    row = sessions_repo.get(sid)
    assert row["cli_session_id"] == "cli_xyz"


def test_heartbeat_updates_last_activity(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    before = sessions_repo.get(sid)["last_activity_at"]
    sessions_repo.touch_activity(sid)
    after = sessions_repo.get(sid)["last_activity_at"]
    assert after >= before


def test_close_marks_status_and_closed_at(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.close(sid, reason="user_clicked")
    row = sessions_repo.get(sid)
    assert row["status"] == "closed"
    assert row["closed_at"] is not None


def test_list_for_user_filters_status(tmp_config):
    init_db()
    uid = seed_user()
    a = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path="p1.md", working_dir=None,
    )
    b = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path="p2.md", working_dir=None,
    )
    sessions_repo.close(a, reason="user")
    open_rows = sessions_repo.list_for_user(uid, statuses=("pending", "active", "idle"))
    assert {r["id"] for r in open_rows} == {b}


def test_list_for_page(tmp_config):
    init_db()
    uid = seed_user()
    a = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path="match.md", working_dir=None,
    )
    sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path="other.md", working_dir=None,
    )
    rows = sessions_repo.list_for_page(user_id=uid, wiki_path="match.md")
    assert {r["id"] for r in rows} == {a}


def test_sweep_marks_idle(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    monkeypatch.setattr(sessions_repo, "_IDLE_SECONDS", 0)
    n = sessions_repo.mark_stale_idle()
    assert n == 1
    row = sessions_repo.get(sid)
    assert row["status"] == "idle"


def test_sweep_marks_closed_after_24h(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    sessions_repo.touch_activity(sid)
    monkeypatch.setattr(sessions_repo, "_IDLE_SECONDS", 0)
    sessions_repo.mark_stale_idle()
    monkeypatch.setattr(sessions_repo, "_CLOSE_AFTER_IDLE_SECONDS", 0)
    n = sessions_repo.evict_idle_to_closed()
    assert n == 1
    row = sessions_repo.get(sid)
    assert row["status"] == "closed"
```

Run:

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_sessions.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 8.3: Write the repo**

Create `backend/app/launchers/sessions.py`:

```python
"""Repo for ``agent_sessions`` — one row per Run-Agent invocation.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select, update

from app.db.models import AgentSession
from app.db.session import session

log = logging.getLogger(__name__)


_IDLE_SECONDS = 300              # 5 min
_CLOSE_AFTER_IDLE_SECONDS = 86400  # 24 h


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "machine_id": row.machine_id,
        "tool_id": row.tool_id,
        "wiki_path": row.wiki_path,
        "working_dir": row.working_dir,
        "first_turn_prompt": row.first_turn_prompt,
        "cli_session_id": row.cli_session_id,
        "status": row.status,
        "started_at": row.started_at,
        "last_activity_at": row.last_activity_at,
        "closed_at": row.closed_at,
    }


# --------------------------------------------------------------------------- #
# Create / read                                                               #
# --------------------------------------------------------------------------- #


def create(
    *,
    user_id: str,
    tool_id: str,
    first_turn_prompt: str,
    wiki_path: str | None,
    working_dir: str | None,
) -> str:
    sid = "as_" + uuid.uuid4().hex
    with session() as s:
        s.add(
            AgentSession(
                id=sid,
                user_id=user_id,
                tool_id=tool_id,
                wiki_path=wiki_path,
                working_dir=working_dir,
                first_turn_prompt=first_turn_prompt,
            )
        )
    log.info("agent_session created id=%s user=%s tool=%s", sid, user_id, tool_id)
    return sid


def get(sid: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(AgentSession, sid)
        return _to_dict(row) if row is not None else None


def list_for_user(
    user_id: str, *, statuses: Iterable[str] = ("pending", "active", "idle"),
) -> list[dict[str, Any]]:
    statuses = tuple(statuses)
    with session() as s:
        rows = s.scalars(
            select(AgentSession)
            .where(AgentSession.user_id == user_id, AgentSession.status.in_(statuses))
            .order_by(AgentSession.started_at.desc())
        ).all()
        return [_to_dict(r) for r in rows]


def list_for_page(*, user_id: str, wiki_path: str) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(AgentSession)
            .where(
                AgentSession.user_id == user_id,
                AgentSession.wiki_path == wiki_path,
                AgentSession.status.in_(("pending", "active", "idle")),
            )
            .order_by(AgentSession.started_at.desc())
        ).all()
        return [_to_dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Mutate                                                                      #
# --------------------------------------------------------------------------- #


def mark_active(sid: str, *, machine_id: str) -> None:
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(status="active", machine_id=machine_id, last_activity_at=_now_iso())
        )


def set_cli_session_id(sid: str, cli_session_id: str) -> None:
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(cli_session_id=cli_session_id, last_activity_at=_now_iso())
        )


def touch_activity(sid: str) -> None:
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(last_activity_at=_now_iso())
        )


def close(sid: str, *, reason: str) -> None:
    now = _now_iso()
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(status="closed", closed_at=now, last_activity_at=now)
        )
    log.info("agent_session closed id=%s reason=%s", sid, reason)


def mark_failed(sid: str, *, reason: str) -> None:
    now = _now_iso()
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(status="failed", closed_at=now, last_activity_at=now)
        )
    log.info("agent_session failed id=%s reason=%s", sid, reason)


# --------------------------------------------------------------------------- #
# Sweep                                                                       #
# --------------------------------------------------------------------------- #


def mark_stale_idle() -> int:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=_IDLE_SECONDS))
    with session() as s:
        result = s.execute(
            update(AgentSession)
            .where(
                AgentSession.status == "active",
                AgentSession.last_activity_at < cutoff,
            )
            .values(status="idle")
        )
        return int(result.rowcount or 0)


def evict_idle_to_closed() -> int:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=_CLOSE_AFTER_IDLE_SECONDS))
    now = _now_iso()
    with session() as s:
        result = s.execute(
            update(AgentSession)
            .where(
                AgentSession.status == "idle",
                AgentSession.last_activity_at < cutoff,
            )
            .values(status="closed", closed_at=now)
        )
        return int(result.rowcount or 0)
```

- [ ] **Step 8.4: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_sessions.py -v
```

Expected: 9 passed.

- [ ] **Step 8.5: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/__init__.py backend/app/launchers/sessions.py backend/tests/test_launchers_sessions.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): agent_sessions repo + tests"
```

---

## Task 9: `page_working_dirs` repo

**Files:**

- Create: `backend/app/launchers/page_dirs.py`
- Test: `backend/tests/test_launchers_page_dirs.py`

- [ ] **Step 9.1: Write the failing test**

Create `backend/tests/test_launchers_page_dirs.py`:

```python
"""Repo for per-(user, machine, page) working directory binding."""
from __future__ import annotations

from app.db.session import init_db
from app.launchers import page_dirs

from tests._seed import seed_user


def test_set_then_get(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid, machine_id="m_a", wiki_path="docs/x.md", working_dir="/home/u/proj",
    )
    row = page_dirs.get_for_page(user_id=uid, machine_id="m_a", wiki_path="docs/x.md")
    assert row == "/home/u/proj"


def test_get_returns_none_when_unset(tmp_config):
    init_db()
    uid = seed_user()
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") is None


def test_set_is_upsert(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid, machine_id="m", wiki_path="x.md", working_dir="/a",
    )
    page_dirs.set_for_page(
        user_id=uid, machine_id="m", wiki_path="x.md", working_dir="/b",
    )
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") == "/b"


def test_per_machine_isolation(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid, machine_id="m_laptop", wiki_path="x.md", working_dir="/home/u/proj",
    )
    page_dirs.set_for_page(
        user_id=uid, machine_id="m_desktop", wiki_path="x.md", working_dir="/work/proj",
    )
    assert page_dirs.get_for_page(user_id=uid, machine_id="m_laptop", wiki_path="x.md") == "/home/u/proj"
    assert page_dirs.get_for_page(user_id=uid, machine_id="m_desktop", wiki_path="x.md") == "/work/proj"


def test_per_user_isolation(tmp_config):
    init_db()
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    page_dirs.set_for_page(
        user_id=a, machine_id="m", wiki_path="x.md", working_dir="/a",
    )
    page_dirs.set_for_page(
        user_id=b, machine_id="m", wiki_path="x.md", working_dir="/b",
    )
    assert page_dirs.get_for_page(user_id=a, machine_id="m", wiki_path="x.md") == "/a"
    assert page_dirs.get_for_page(user_id=b, machine_id="m", wiki_path="x.md") == "/b"


def test_clear(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid, machine_id="m", wiki_path="x.md", working_dir="/p",
    )
    page_dirs.clear(user_id=uid, machine_id="m", wiki_path="x.md")
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") is None
```

Run, expect FAIL.

- [ ] **Step 9.2: Write the repo**

Create `backend/app/launchers/page_dirs.py`:

```python
"""Repo for ``page_working_dirs`` — per-(user, machine, page) working directory binding."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import PageWorkingDir
from app.db.session import session


def get_for_page(*, user_id: str, machine_id: str, wiki_path: str) -> str | None:
    with session() as s:
        row = s.scalar(
            select(PageWorkingDir).where(
                PageWorkingDir.user_id == user_id,
                PageWorkingDir.machine_id == machine_id,
                PageWorkingDir.wiki_path == wiki_path,
            )
        )
        return row.working_dir if row is not None else None


def set_for_page(*, user_id: str, machine_id: str, wiki_path: str, working_dir: str) -> None:
    stmt = pg_insert(PageWorkingDir).values(
        user_id=user_id,
        machine_id=machine_id,
        wiki_path=wiki_path,
        working_dir=working_dir,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "machine_id", "wiki_path"],
        set_={"working_dir": stmt.excluded.working_dir},
    )
    with session() as s:
        s.execute(stmt)


def clear(*, user_id: str, machine_id: str, wiki_path: str) -> None:
    with session() as s:
        s.execute(
            delete(PageWorkingDir).where(
                PageWorkingDir.user_id == user_id,
                PageWorkingDir.machine_id == machine_id,
                PageWorkingDir.wiki_path == wiki_path,
            )
        )
```

- [ ] **Step 9.3: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_page_dirs.py -v
```

Expected: 6 passed.

- [ ] **Step 9.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/page_dirs.py backend/tests/test_launchers_page_dirs.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): page_working_dirs repo + tests"
```

---

## Task 10: Manifest pydantic model + DSL validator

**Files:**

- Create: `backend/app/launchers/registry.py`
- Test: `backend/tests/test_launchers_registry.py`

- [ ] **Step 10.1: Write the failing test**

Create `backend/tests/test_launchers_registry.py`:

```python
"""Manifest pydantic model + DSL validator.

Validator rules (per coding_tool_launchers/design.md):
- ``${token}`` must NOT appear in any ``launch.argv`` or ``resume.argv`` element.
- ``${first_turn_prompt}`` must NOT appear anywhere (helper materializes a tmpfile).
- Unknown ``${var}`` names are rejected.
- ``launch.binary`` must be present (allow-list cross-check is helper-side, but the registry can warn).
- Manifest version != 1 is rejected at load time.
"""
from __future__ import annotations

import json

import pytest

from app.launchers import registry


def _valid_claude_manifest() -> dict:
    return {
        "manifest_version": 1,
        "id": "claude-code",
        "name": "Claude Code",
        "tagline": "Anthropic's terminal coding agent.",
        "icon_url": "/icons/claude-code.svg",
        "kind": "local_cli",
        "cli_check": {
            "binary": "claude",
            "version_flag": "--version",
            "min_version": "1.0.0",
            "install_hint_url": "https://example.com",
        },
        "mcp_config_format": "claude_json",
        "first_turn_prompt_delivery": {"method": "prompt_file_flag", "flag": "--prompt-file"},
        "launch": {
            "binary": "claude",
            "argv": ["--mcp-config", "${mcp_config_path}"],
            "env": {
                "AGENTWIKI_SESSION_ID": "${session_id}",
                "AGENTWIKI_ENDPOINT": "${endpoint}",
                "AGENTWIKI_MCP_TOKEN": "${token}",
            },
            "cwd": "${working_dir}",
        },
        "resume": {
            "argv": ["--resume", "${cli_session_id}", "--mcp-config", "${mcp_config_path}"],
            "env": {"AGENTWIKI_SESSION_ID": "${session_id}", "AGENTWIKI_MCP_TOKEN": "${token}"},
            "cwd": "${working_dir}",
        },
        "session_id_capture": {
            "source": "file_watch",
            "path": "${home}/.claude/projects/${dirhash}/",
            "pattern": "*.jsonl",
            "extract": "filename_basename",
        },
    }


def test_valid_manifest_parses():
    m = registry.Manifest.model_validate(_valid_claude_manifest())
    assert m.id == "claude-code"
    assert m.kind == "local_cli"


def test_unknown_var_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("${not_a_var}")
    with pytest.raises(ValueError, match="unknown interpolation var"):
        registry.Manifest.model_validate(bad)


def test_token_in_argv_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("Bearer ${token}")
    with pytest.raises(ValueError, match="\\$\\{token\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_first_turn_prompt_anywhere_rejected():
    bad = _valid_claude_manifest()
    bad["launch"]["argv"].append("${first_turn_prompt}")
    with pytest.raises(ValueError, match="\\$\\{first_turn_prompt\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_first_turn_prompt_in_resume_rejected():
    bad = _valid_claude_manifest()
    bad["resume"]["argv"].append("${first_turn_prompt}")
    with pytest.raises(ValueError, match="\\$\\{first_turn_prompt\\} forbidden"):
        registry.Manifest.model_validate(bad)


def test_unknown_manifest_version_rejected():
    bad = _valid_claude_manifest()
    bad["manifest_version"] = 2
    with pytest.raises(ValueError, match="manifest_version"):
        registry.Manifest.model_validate(bad)


def test_in_app_kind_skips_launch_block():
    m = registry.Manifest.model_validate({
        "manifest_version": 1,
        "id": "onyx-craft",
        "name": "Onyx Craft",
        "tagline": "in-app",
        "icon_url": "/x.svg",
        "kind": "in_app",
        "task_kind": "craft_agent",
    })
    assert m.kind == "in_app"
    assert m.task_kind == "craft_agent"
    assert m.launch is None
```

Run, expect FAIL.

- [ ] **Step 10.2: Write the registry module**

Create `backend/app/launchers/registry.py`:

```python
"""Manifest pydantic model + DSL validator + on-disk loader.

A "manifest" is a JSON description of one coding tool the wiki can
launch. See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``
for the full spec and rationale.

This module:
- Defines pydantic models that mirror the manifest JSON shape.
- Validates the DSL rules required by the security model:
    * No ``${token}`` in any ``launch.argv`` / ``resume.argv``.
    * No ``${first_turn_prompt}`` anywhere.
    * No unknown ``${var}`` interpolation tokens.
- Loads every ``*.json`` under ``app/launchers/manifests/`` into a
  ``ManifestRegistry`` at import time.

The HELPER-side binary allow-list is intentionally NOT enforced here —
that's the helper's defense layer (so even a backend RCE can't smuggle
``rm`` past the helper). Backend just validates DSL well-formedness.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)


_ALLOWED_VARS = frozenset({
    "token",
    "endpoint",
    "session_id",
    "cli_session_id",
    "working_dir",
    "first_turn_prompt",
    "prompt_file_path",
    "mcp_config_path",
    "home",
    "dirhash",
})

_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


def _find_vars(s: str) -> set[str]:
    return set(_VAR_RE.findall(s))


def _check_string(s: str, *, where: str) -> None:
    used = _find_vars(s)
    unknown = used - _ALLOWED_VARS
    if unknown:
        raise ValueError(f"unknown interpolation var(s) {sorted(unknown)} in {where}")


class CliCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    binary: str
    version_flag: str = "--version"
    min_version: str | None = None
    install_hint_url: str | None = None


class FirstTurnPromptDelivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["prompt_file_flag", "stdin", "none"]
    flag: str | None = None  # required when method == prompt_file_flag


class LaunchBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    binary: str
    argv: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    @model_validator(mode="after")
    def _validate_vars(self) -> "LaunchBlock":
        for i, a in enumerate(self.argv):
            _check_string(a, where=f"argv[{i}]")
            if "${token}" in a:
                raise ValueError(
                    f"${{token}} forbidden in argv (token must come via env "
                    f"AGENTWIKI_MCP_TOKEN). Offending element argv[{i}]={a!r}."
                )
            if "${first_turn_prompt}" in a:
                raise ValueError(
                    f"${{first_turn_prompt}} forbidden anywhere — helper "
                    f"materializes a tmpfile; reference ${{prompt_file_path}} "
                    f"instead. Offending element argv[{i}]={a!r}."
                )
        for k, v in self.env.items():
            _check_string(v, where=f"env.{k}")
            if "${first_turn_prompt}" in v:
                raise ValueError(
                    f"${{first_turn_prompt}} forbidden anywhere — reference "
                    f"${{prompt_file_path}} instead. Offending env.{k}={v!r}."
                )
        if self.cwd is not None:
            _check_string(self.cwd, where="cwd")
            if "${first_turn_prompt}" in self.cwd:
                raise ValueError("${first_turn_prompt} forbidden in cwd")
        return self


class ResumeBlock(LaunchBlock):
    """Same shape as LaunchBlock; validator enforces NO first_turn_prompt anywhere (already inherited)."""


class SessionIdCapture(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["file_watch", "stdout_regex", "none"]
    path: str | None = None
    pattern: str | None = None
    extract: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_version: Literal[1]
    id: str
    name: str
    tagline: str
    icon_url: str
    kind: Literal["local_cli", "in_app", "web_handoff"]

    cli_check: CliCheck | None = None
    mcp_config_format: Literal["claude_json", "codex_toml", "none"] | None = None
    first_turn_prompt_delivery: FirstTurnPromptDelivery | None = None
    launch: LaunchBlock | None = None
    resume: ResumeBlock | None = None
    session_id_capture: SessionIdCapture | None = None

    # in_app-only
    task_kind: str | None = None
    stream_resource_uri: str | None = None

    @model_validator(mode="after")
    def _validate_kind_shape(self) -> "Manifest":
        if self.kind == "local_cli":
            if self.launch is None:
                raise ValueError("local_cli manifest must have launch block")
            if self.cli_check is None:
                raise ValueError("local_cli manifest must have cli_check")
            if self.first_turn_prompt_delivery is None:
                raise ValueError("local_cli manifest must specify first_turn_prompt_delivery")
        elif self.kind == "in_app":
            if self.task_kind is None:
                raise ValueError("in_app manifest must specify task_kind")
        return self


# --------------------------------------------------------------------------- #
# Registry — loads manifests at import time                                   #
# --------------------------------------------------------------------------- #


class ManifestRegistry:
    def __init__(self, manifest_dir: Path):
        self._by_id: dict[str, Manifest] = {}
        for p in sorted(manifest_dir.glob("*.json")):
            try:
                m = Manifest.model_validate_json(p.read_text())
            except Exception:
                log.exception("manifest %s failed validation; refusing to load", p)
                raise
            if m.id in self._by_id:
                raise ValueError(f"duplicate manifest id {m.id!r}")
            self._by_id[m.id] = m
            log.info("manifest loaded id=%s kind=%s", m.id, m.kind)

    def list(self) -> list[Manifest]:
        return list(self._by_id.values())

    def get(self, manifest_id: str) -> Manifest | None:
        return self._by_id.get(manifest_id)


_MANIFEST_DIR = Path(__file__).parent / "manifests"
_registry_singleton: ManifestRegistry | None = None


def get_registry() -> ManifestRegistry:
    """Lazy singleton — defers manifest dir read until first use so the
    test file can import the validator types without the manifest dir
    needing to exist yet."""
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = ManifestRegistry(_MANIFEST_DIR)
    return _registry_singleton
```

Use `get_registry()` everywhere else in the code (Tasks 11 / 15 / 17 already reference it).

- [ ] **Step 10.3: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_registry.py -v
```

Expected: 7 passed. (The manifest dir doesn't exist yet, but `get_registry()` is only called lazily — the registry tests construct `Manifest` directly and never touch the singleton.)

- [ ] **Step 10.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/registry.py backend/tests/test_launchers_registry.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): manifest pydantic model + DSL validator"
```

---

## Task 11: Ship the three manifest JSONs

**Files:**

- Create: `backend/app/launchers/manifests/claude_code.json`
- Create: `backend/app/launchers/manifests/codex.json`
- Create: `backend/app/launchers/manifests/onyx_craft.json`
- Test: `backend/tests/test_launchers_manifests.py`

- [ ] **Step 11.1: Write the failing test**

Create `backend/tests/test_launchers_manifests.py`:

```python
"""All shipped manifests load + validate."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.launchers.registry import ManifestRegistry, get_registry


def test_all_shipped_manifests_load():
    r = get_registry()
    ids = {m.id for m in r.list()}
    assert ids == {"claude-code", "codex", "onyx-craft"}


def test_claude_manifest_passes_token_argv_rule():
    r = get_registry()
    m = r.get("claude-code")
    assert m is not None
    for a in m.launch.argv:
        assert "${token}" not in a
        assert "${first_turn_prompt}" not in a


def test_codex_manifest_passes_token_argv_rule():
    r = get_registry()
    m = r.get("codex")
    assert m is not None
    for a in m.launch.argv:
        assert "${token}" not in a
        assert "${first_turn_prompt}" not in a


def test_codex_uses_file_watch_capture():
    """P1 #4 — codex's session capture must be file_watch, never stdout_regex."""
    r = get_registry()
    m = r.get("codex")
    assert m.session_id_capture.source == "file_watch"


def test_onyx_craft_is_in_app():
    r = get_registry()
    m = r.get("onyx-craft")
    assert m.kind == "in_app"
    assert m.task_kind == "craft_agent"
    assert m.launch is None
```

Run, expect FAIL.

- [ ] **Step 11.2: Write `claude_code.json`**

Create `backend/app/launchers/manifests/claude_code.json`:

```json
{
  "manifest_version": 1,
  "id": "claude-code",
  "name": "Claude Code",
  "tagline": "Anthropic's terminal coding agent.",
  "icon_url": "/icons/claude-code.svg",
  "kind": "local_cli",
  "cli_check": {
    "binary": "claude",
    "version_flag": "--version",
    "min_version": "1.0.0",
    "install_hint_url": "https://docs.claude.com/code/install"
  },
  "mcp_config_format": "claude_json",
  "first_turn_prompt_delivery": {
    "method": "prompt_file_flag",
    "flag": "--prompt-file"
  },
  "launch": {
    "binary": "claude",
    "argv": ["--mcp-config", "${mcp_config_path}"],
    "env": {
      "AGENTWIKI_SESSION_ID": "${session_id}",
      "AGENTWIKI_ENDPOINT": "${endpoint}",
      "AGENTWIKI_MCP_TOKEN": "${token}"
    },
    "cwd": "${working_dir}"
  },
  "resume": {
    "binary": "claude",
    "argv": [
      "--resume",
      "${cli_session_id}",
      "--mcp-config",
      "${mcp_config_path}"
    ],
    "env": {
      "AGENTWIKI_SESSION_ID": "${session_id}",
      "AGENTWIKI_MCP_TOKEN": "${token}"
    },
    "cwd": "${working_dir}"
  },
  "session_id_capture": {
    "source": "file_watch",
    "path": "${home}/.claude/projects/${dirhash}/",
    "pattern": "*.jsonl",
    "extract": "filename_basename"
  }
}
```

- [ ] **Step 11.3: Write `codex.json`**

Create `backend/app/launchers/manifests/codex.json`:

```json
{
  "manifest_version": 1,
  "id": "codex",
  "name": "Codex",
  "tagline": "OpenAI's terminal coding agent.",
  "icon_url": "/icons/codex.svg",
  "kind": "local_cli",
  "cli_check": {
    "binary": "codex",
    "version_flag": "--version",
    "min_version": "0.10.0",
    "install_hint_url": "https://github.com/openai/codex#install"
  },
  "mcp_config_format": "codex_toml",
  "first_turn_prompt_delivery": {
    "method": "prompt_file_flag",
    "flag": "--prompt-file"
  },
  "launch": {
    "binary": "codex",
    "argv": ["--config-file", "${mcp_config_path}"],
    "env": {
      "AGENTWIKI_SESSION_ID": "${session_id}",
      "AGENTWIKI_ENDPOINT": "${endpoint}",
      "AGENTWIKI_MCP_TOKEN": "${token}"
    },
    "cwd": "${working_dir}"
  },
  "resume": {
    "binary": "codex",
    "argv": [
      "resume",
      "${cli_session_id}",
      "--config-file",
      "${mcp_config_path}"
    ],
    "env": {
      "AGENTWIKI_SESSION_ID": "${session_id}",
      "AGENTWIKI_MCP_TOKEN": "${token}"
    },
    "cwd": "${working_dir}"
  },
  "session_id_capture": {
    "source": "file_watch",
    "path": "${home}/.codex/sessions/",
    "pattern": "*.json",
    "extract": "filename_basename"
  }
}
```

Note the placeholder paths/flags. Phase 3 (helper + real CLI) will verify and patch these against `codex --help` / `claude --help` output and adjust manifests as needed. The DSL contract is what matters here.

- [ ] **Step 11.4: Write `onyx_craft.json`**

Create `backend/app/launchers/manifests/onyx_craft.json`:

```json
{
  "manifest_version": 1,
  "id": "onyx-craft",
  "name": "Onyx Craft",
  "tagline": "In-app agent that drafts and edits docs.",
  "icon_url": "/icons/onyx-craft.svg",
  "kind": "in_app",
  "task_kind": "craft_agent",
  "stream_resource_uri": "job://${session_id}"
}
```

- [ ] **Step 11.5: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_manifests.py -v
```

Expected: 5 passed.

- [ ] **Step 11.6: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/manifests backend/tests/test_launchers_manifests.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): ship claude_code / codex / onyx_craft manifests"
```

---

## Task 12: Frontmatter `linked_repos` parser

**Files:**

- Create: `backend/app/wiki/linked_repos.py`
- Test: `backend/tests/test_linked_repos.py`

- [ ] **Step 12.1: Write the failing test**

Create `backend/tests/test_linked_repos.py`:

```python
"""Parse linked_repos from page frontmatter."""
from __future__ import annotations

import pytest

from app.wiki.linked_repos import parse_linked_repos


def test_no_frontmatter_returns_empty():
    body = "# A doc\n\nNo frontmatter."
    assert parse_linked_repos(body) == []


def test_frontmatter_no_linked_repos_key():
    body = "---\ntitle: x\n---\n# Body"
    assert parse_linked_repos(body) == []


def test_single_repo_string():
    body = """---
linked_repos:
  - git@github.com:onyx-dot-app/onyx
---
# Body"""
    assert parse_linked_repos(body) == ["git@github.com:onyx-dot-app/onyx"]


def test_multiple_repos():
    body = """---
linked_repos:
  - git@github.com:onyx-dot-app/onyx
  - git@github.com:onyx-dot-app/agent-wiki
---
# Body"""
    assert parse_linked_repos(body) == [
        "git@github.com:onyx-dot-app/onyx",
        "git@github.com:onyx-dot-app/agent-wiki",
    ]


def test_non_list_value_returns_empty():
    body = "---\nlinked_repos: not-a-list\n---\n# Body"
    assert parse_linked_repos(body) == []


def test_non_string_items_filtered():
    body = """---
linked_repos:
  - git@github.com:a/b
  - 42
  - null
---
# Body"""
    assert parse_linked_repos(body) == ["git@github.com:a/b"]


def test_malformed_frontmatter_returns_empty():
    body = "---\nnot: yaml: at: all:::\n---\n# Body"
    assert parse_linked_repos(body) == []
```

Run, expect FAIL.

- [ ] **Step 12.2: Write the parser**

Create `backend/app/wiki/linked_repos.py`:

```python
"""Extract the ``linked_repos`` list from a markdown doc's YAML frontmatter.

Returns ``[]`` on any of:
- no frontmatter block,
- frontmatter present but no ``linked_repos`` key,
- malformed YAML,
- value isn't a list,
- list contains non-string items (those items are silently filtered).

Repos URLs are project metadata and shared across users; per-user
checkout paths live in ``page_working_dirs`` (Postgres-side, NOT in
git). See ``coding_tool_launchers/design.md``.
"""
from __future__ import annotations

import logging
import re

import yaml

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)


def parse_linked_repos(body: str) -> list[str]:
    match = _FRONTMATTER_RE.match(body)
    if match is None:
        return []
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("linked_repos")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]
```

- [ ] **Step 12.3: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_linked_repos.py -v
```

Expected: 7 passed.

- [ ] **Step 12.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/wiki/linked_repos.py backend/tests/test_linked_repos.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): linked_repos frontmatter parser"
```

---

## Task 13: First-turn prompt builder

**Files:**

- Create: `backend/app/launchers/prompt_builder.py`
- Test: `backend/tests/test_launchers_prompt_builder.py`

- [ ] **Step 13.1: Write the failing test**

Create `backend/tests/test_launchers_prompt_builder.py`:

```python
"""Compose the first-turn prompt for a Run-Agent invocation.

Composition (per coding_tool_launchers/design.md):
1. WIKI_PATH line — points the agent at the doc it's working from.
2. WORKING_DIRECTORY line — tells the agent where it's running.
3. LINKED_REPOS line(s) — repo URLs from frontmatter, if any.
4. PAGE_BODY block — the wiki page content.
5. USER_MESSAGE block — the message the user typed in the wizard.
"""
from __future__ import annotations

from app.launchers.prompt_builder import build_first_turn_prompt


def test_minimal_composition():
    p = build_first_turn_prompt(
        wiki_path=None,
        page_body=None,
        working_dir=None,
        linked_repos=[],
        user_message="audit auth",
    )
    assert "USER_MESSAGE" in p
    assert "audit auth" in p


def test_with_all_fields():
    p = build_first_turn_prompt(
        wiki_path="projects/auth.md",
        page_body="# Auth refactor\n\nDetails here.",
        working_dir="/Users/u/code/onyx",
        linked_repos=["git@github.com:onyx-dot-app/onyx"],
        user_message="kick off the refactor",
    )
    assert "WIKI_PATH: projects/auth.md" in p
    assert "WORKING_DIRECTORY: /Users/u/code/onyx" in p
    assert "LINKED_REPOS:" in p
    assert "git@github.com:onyx-dot-app/onyx" in p
    assert "Auth refactor" in p
    assert "kick off the refactor" in p


def test_no_wiki_path_skips_line():
    p = build_first_turn_prompt(
        wiki_path=None, page_body=None, working_dir="/tmp", linked_repos=[],
        user_message="x",
    )
    assert "WIKI_PATH" not in p


def test_no_linked_repos_skips_section():
    p = build_first_turn_prompt(
        wiki_path="x.md", page_body="body", working_dir=None,
        linked_repos=[], user_message="m",
    )
    assert "LINKED_REPOS" not in p


def test_page_body_section_only_when_provided():
    p = build_first_turn_prompt(
        wiki_path="x.md", page_body=None, working_dir=None, linked_repos=[],
        user_message="m",
    )
    assert "PAGE_BODY" not in p
```

Run, expect FAIL.

- [ ] **Step 13.2: Write the builder**

Create `backend/app/launchers/prompt_builder.py`:

```python
"""Compose the first-turn prompt the spawned CLI sees at t=0.

The prompt is **first-turn only** — never replayed on resume (the CLI
already has its conversation). The output is persisted to
``agent_sessions.first_turn_prompt`` so a resume request can debug
exactly what the agent originally saw.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
"""
from __future__ import annotations


def build_first_turn_prompt(
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
```

- [ ] **Step 13.3: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launchers_prompt_builder.py -v
```

Expected: 5 passed.

- [ ] **Step 13.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/prompt_builder.py backend/tests/test_launchers_prompt_builder.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): first-turn prompt builder"
```

---

## Task 14: HTTP request/response pydantic models

**Files:**

- Create: `backend/app/models/launchers.py`

- [ ] **Step 14.1: Write the schemas**

Create `backend/app/models/launchers.py`:

```python
"""HTTP request/response shapes for the launchers + agent_sessions routers.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``
for what each field is for.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# --------------------------------------------------------------------------- #
# GET /api/launchers — catalog                                                #
# --------------------------------------------------------------------------- #


class LauncherCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    tagline: str
    icon_url: str
    kind: Literal["local_cli", "in_app", "web_handoff"]
    setup_status: dict[str, Any]  # {token: bool, helper_seen_on_any_machine: bool}


class LauncherCatalog(BaseModel):
    launchers: list[LauncherCatalogEntry]


# --------------------------------------------------------------------------- #
# POST /api/launch                                                            #
# --------------------------------------------------------------------------- #


class LaunchRequest(BaseModel):
    tool_id: str
    wiki_path: str | None = None
    working_dir: str | None = None
    message: str
    resume_session_id: str | None = None
    remember_workdir_for_page: bool = False


class LaunchResponse(BaseModel):
    launch_code: str
    uri: str  # agentwiki://run?code=lc_…&tool=…
    agent_session_id: str


# --------------------------------------------------------------------------- #
# POST /api/launch/exchange (helper-facing)                                   #
# --------------------------------------------------------------------------- #


class ExchangeRequest(BaseModel):
    code: str
    machine_id: str


class ExchangePayload(BaseModel):
    session_id: str
    working_dir: str | None
    first_turn_prompt: str | None  # absent on resume
    cli_session_id: str | None  # present only on resume


class ExchangeResponse(BaseModel):
    mcp_token: str
    endpoint: str
    manifest: dict[str, Any]  # full manifest JSON for the helper to interpret
    payload: ExchangePayload


# --------------------------------------------------------------------------- #
# Probe endpoints                                                             #
# --------------------------------------------------------------------------- #


class ProbeAckRequest(BaseModel):
    nonce: str
    helper_port: int


class ProbeStatusResponse(BaseModel):
    acked: bool
    helper_port: int | None


# --------------------------------------------------------------------------- #
# Agent-session endpoints                                                     #
# --------------------------------------------------------------------------- #


class AgentSessionSummary(BaseModel):
    id: str
    tool_id: str
    wiki_path: str | None
    working_dir: str | None
    status: str
    started_at: str
    last_activity_at: str
    closed_at: str | None
    cli_session_id: str | None


class AgentSessionList(BaseModel):
    sessions: list[AgentSessionSummary]


class CliSessionUpdateRequest(BaseModel):
    cli_session_id: str


class CloseRequest(BaseModel):
    reason: str | None = None
```

- [ ] **Step 14.2: Verify**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev python -c "from app.models.launchers import LaunchResponse, ExchangeResponse; print('ok')"
```

Expected: `ok`

- [ ] **Step 14.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/models/launchers.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): HTTP request/response schemas"
```

---

## Task 15: `GET /api/launchers` (catalog)

**Files:**

- Create: `backend/app/api/launchers.py`
- Test: `backend/tests/test_launch_api.py` (initial scaffold)

- [ ] **Step 15.1: Scaffold the test file**

Create `backend/tests/test_launch_api.py`:

```python
"""Launchers API surface.

Tests build the FastAPI app via ``create_app()`` and a real per-test
Postgres schema. No mocking of repos, no mocking of the manifest
registry — everything below the HTTP boundary is real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


def test_get_catalog_requires_auth(client):
    res = client.get("/api/launchers")
    assert res.status_code in (401, 403)


def test_get_catalog_returns_all_manifests(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launchers")
    assert res.status_code == 200
    ids = {x["id"] for x in res.json()["launchers"]}
    assert ids == {"claude-code", "codex", "onyx-craft"}


def test_setup_status_token_false_when_no_tokens(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launchers")
    for entry in res.json()["launchers"]:
        assert entry["setup_status"]["token"] is False


def test_setup_status_token_true_after_mint(client):
    uid = seed_user()
    login_fastapi(client, uid)
    tokens_repo.create(uid, "k")
    res = client.get("/api/launchers")
    for entry in res.json()["launchers"]:
        assert entry["setup_status"]["token"] is True
```

Run, expect FAIL — route doesn't exist.

- [ ] **Step 15.2: Write the router (catalog only)**

Create `backend/app/api/launchers.py`:

```python
"""HTTP API for coding-tool launchers.

Routes mounted under ``/api/launchers`` and ``/api/launch`` from
``app.main:create_app``. See ``local_data/wiki/Wiki Project/Specific
Features/coding_tool_launchers/design.md`` for the full surface.

All routes gated by ``CONFIG.launchers_enabled`` — when off, return 404.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import User
from app.auth import mcp_tokens as tokens_repo
from app.auth.deps import require_user
from app.config import CONFIG
from app.launchers.registry import get_registry
from app.models.launchers import LauncherCatalog, LauncherCatalogEntry

log = logging.getLogger(__name__)

router = APIRouter()


def _check_flag() -> None:
    if not CONFIG.launchers_enabled:
        raise HTTPException(status_code=404, detail="launchers disabled")


@router.get("/launchers", response_model=LauncherCatalog)
def get_catalog(user: User = Depends(require_user)) -> LauncherCatalog:
    _check_flag()
    has_token = len(tokens_repo.list_for_user(user.id)) > 0
    entries: list[LauncherCatalogEntry] = []
    for m in get_registry().list():
        entries.append(
            LauncherCatalogEntry(
                id=m.id,
                name=m.name,
                tagline=m.tagline,
                icon_url=m.icon_url,
                kind=m.kind,
                setup_status={"token": has_token},
            )
        )
    return LauncherCatalog(launchers=entries)
```

- [ ] **Step 15.3: Register the router in `main.py`**

Open `backend/app/main.py`. Find the section with `app.include_router(...)` calls (around line 160). After the existing routers, add:

```python
    from app.api import launchers as launchers_router
    app.include_router(launchers_router.router, prefix="/api")
```

- [ ] **Step 15.4: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_api.py -v
```

Expected: 4 passed.

- [ ] **Step 15.5: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/api/launchers.py backend/app/main.py backend/tests/test_launch_api.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): GET /api/launchers (catalog)"
```

---

## Task 16: `POST /api/launch` (mint launch code + session)

**Files:**

- Modify: `backend/app/api/launchers.py`
- Modify: `backend/tests/test_launch_api.py`

- [ ] **Step 16.1: Extend the test file**

Append to `backend/tests/test_launch_api.py`:

```python
def test_post_launch_creates_session_and_returns_uri(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post("/api/launch", json={
        "tool_id": "claude-code",
        "wiki_path": "x.md",
        "message": "do the thing",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["agent_session_id"].startswith("as_")
    assert body["launch_code"].startswith("lc_")
    assert body["uri"].startswith("agentwiki://run?")
    assert f"code={body['launch_code']}" in body["uri"]
    assert "tool=claude-code" in body["uri"]


def test_post_launch_auto_mints_token_when_none_exists(client):
    uid = seed_user()
    login_fastapi(client, uid)
    assert tokens_repo.list_for_user(uid) == []
    res = client.post("/api/launch", json={
        "tool_id": "claude-code",
        "wiki_path": None,
        "message": "x",
    })
    assert res.status_code == 200
    rows = tokens_repo.list_for_user(uid)
    assert len(rows) == 1
    assert rows[0]["name"].startswith("launcher-")


def test_post_launch_unknown_tool_returns_404(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post("/api/launch", json={
        "tool_id": "does-not-exist",
        "wiki_path": None,
        "message": "x",
    })
    assert res.status_code == 404


def test_post_launch_in_app_kind_returns_400_for_now(client):
    """in_app launches route through POST /api/craft/launch (separate endpoint per P2 #16).
    POST /api/launch refuses in_app tool_ids."""
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post("/api/launch", json={
        "tool_id": "onyx-craft",
        "wiki_path": None,
        "message": "x",
    })
    assert res.status_code == 400
```

- [ ] **Step 16.2: Implement `POST /api/launch`**

Extend `backend/app/api/launchers.py`:

```python
# (Append below the existing get_catalog route.)

from fastapi import Request

from app.auth import launch_codes as codes_repo
from app.launchers import page_dirs, prompt_builder, sessions as sessions_repo
from app.models.launchers import LaunchRequest, LaunchResponse
from app.wiki import git as wiki_git, linked_repos


@router.post("/launch", response_model=LaunchResponse)
def post_launch(req: LaunchRequest, request: Request, user: User = Depends(require_user)) -> LaunchResponse:
    _check_flag()

    manifest = get_registry().get(req.tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"unknown tool_id {req.tool_id!r}")
    if manifest.kind != "local_cli":
        # in_app routes through /api/craft/launch (separate endpoint, P2 #16).
        # web_handoff isn't implemented yet either.
        raise HTTPException(
            status_code=400,
            detail=f"tool {req.tool_id!r} kind={manifest.kind!r} not supported by this endpoint",
        )

    # Resume support — short-circuit prompt building.
    first_turn_prompt: str
    if req.resume_session_id is not None:
        existing = sessions_repo.get(req.resume_session_id)
        if existing is None or existing["user_id"] != user.id:
            raise HTTPException(status_code=404, detail="resume session not found")
        first_turn_prompt = ""  # never re-injected on resume (P1 #6).
        wiki_path = existing["wiki_path"]
        working_dir = existing["working_dir"]
    else:
        page_body = _maybe_read_page_body(req.wiki_path)
        repos = linked_repos.parse_linked_repos(page_body) if page_body else []
        first_turn_prompt = prompt_builder.build_first_turn_prompt(
            wiki_path=req.wiki_path,
            page_body=page_body,
            working_dir=req.working_dir,
            linked_repos=repos,
            user_message=req.message,
        )
        wiki_path = req.wiki_path
        working_dir = req.working_dir

    sid = sessions_repo.create(
        user_id=user.id,
        tool_id=req.tool_id,
        first_turn_prompt=first_turn_prompt,
        wiki_path=wiki_path,
        working_dir=working_dir,
    )

    # Auto-mint a launcher MCP token if the user has none.
    existing_tokens = tokens_repo.list_for_user(user.id)
    if existing_tokens:
        token_id = existing_tokens[0]["id"]
    else:
        token_id, _ = tokens_repo.create(user.id, name=f"launcher-{req.tool_id}")

    code = codes_repo.create(user_id=user.id, agent_session_id=sid, mcp_token_id=token_id)

    endpoint = str(request.base_url).rstrip("/") + "/api/mcp"
    uri = f"agentwiki://run?code={code}&tool={req.tool_id}&endpoint={endpoint}"

    return LaunchResponse(launch_code=code, uri=uri, agent_session_id=sid)


def _maybe_read_page_body(wiki_path: str | None) -> str | None:
    if not wiki_path:
        return None
    try:
        return wiki_git.read_file(wiki_path)
    except FileNotFoundError:
        return None
```

**Verify `wiki_git.read_file` exists.** Before this step, run:

```bash
grep -n "^def read_file\|^def read\b\|^def get_file\|^def show\b" /Users/nikolas/agent-wiki/backend/app/wiki/git.py
```

Use whichever function name the file actually exports for "read a doc body at HEAD". Common alternatives in this codebase: `read_file(rel_path)`, `read(rel_path)`, `show(rel_path, ref="HEAD")`. Patch the import + call in `_maybe_read_page_body` to match. If no such function exists (only `commit_file` and friends), use the lower-level wiki read API instead — check `app/wiki/edit.py` for a doc-loader helper.

- [ ] **Step 16.3: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_api.py -v
```

Expected: 8 passed.

- [ ] **Step 16.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/api/launchers.py backend/tests/test_launch_api.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): POST /api/launch (mint code + session)"
```

---

## Task 17: `POST /api/launch/exchange` (helper-facing)

**Files:**

- Modify: `backend/app/api/launchers.py`
- Modify: `backend/tests/test_launch_api.py`

- [ ] **Step 17.1: Append tests**

```python
def test_exchange_consumes_code_and_returns_token(client):
    uid = seed_user()
    login_fastapi(client, uid)
    launch_res = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": None, "message": "x",
    })
    code = launch_res.json()["launch_code"]
    # Helper is unauthenticated to the cookie path — uses the launch code itself.
    fresh = TestClient(create_app())
    res = fresh.post("/api/launch/exchange", json={
        "code": code, "machine_id": "m_abc",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["mcp_token"].startswith("mcp_")
    assert body["manifest"]["id"] == "claude-code"
    assert body["payload"]["first_turn_prompt"] is not None
    assert body["payload"]["session_id"].startswith("as_")


def test_exchange_unknown_code_returns_404(client):
    fresh = TestClient(create_app())
    res = fresh.post("/api/launch/exchange", json={
        "code": "lc_does_not_exist", "machine_id": "m",
    })
    assert res.status_code == 404


def test_exchange_consumed_code_returns_409(client):
    uid = seed_user()
    login_fastapi(client, uid)
    code = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": None, "message": "x",
    }).json()["launch_code"]
    fresh = TestClient(create_app())
    fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    res = fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    assert res.status_code == 409


def test_exchange_expired_code_returns_410(client, monkeypatch):
    from app.auth import launch_codes as codes_repo
    monkeypatch.setattr(codes_repo, "_TTL_SECONDS", 0)
    uid = seed_user()
    login_fastapi(client, uid)
    code = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": None, "message": "x",
    }).json()["launch_code"]
    fresh = TestClient(create_app())
    res = fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    assert res.status_code == 410


def test_exchange_transitions_session_to_active_with_machine_id(client):
    from app.launchers import sessions as sessions_repo
    uid = seed_user()
    login_fastapi(client, uid)
    launch_body = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": None, "message": "x",
    }).json()
    fresh = TestClient(create_app())
    fresh.post("/api/launch/exchange", json={
        "code": launch_body["launch_code"], "machine_id": "m_xyz",
    })
    row = sessions_repo.get(launch_body["agent_session_id"])
    assert row["status"] == "active"
    assert row["machine_id"] == "m_xyz"


def test_exchange_omits_first_turn_prompt_on_resume(client):
    from app.launchers import sessions as sessions_repo
    uid = seed_user()
    login_fastapi(client, uid)
    # First launch.
    first_body = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": "x.md", "message": "go",
    }).json()
    fresh = TestClient(create_app())
    fresh.post("/api/launch/exchange", json={
        "code": first_body["launch_code"], "machine_id": "m",
    })
    sessions_repo.set_cli_session_id(first_body["agent_session_id"], "cli_xyz")

    # Resume.
    resume_body = client.post("/api/launch", json={
        "tool_id": "claude-code",
        "wiki_path": None,
        "message": "ignored on resume",
        "resume_session_id": first_body["agent_session_id"],
    }).json()
    res = fresh.post("/api/launch/exchange", json={
        "code": resume_body["launch_code"], "machine_id": "m",
    })
    payload = res.json()["payload"]
    assert payload["first_turn_prompt"] is None  # P1 #6
    assert payload["cli_session_id"] == "cli_xyz"
```

- [ ] **Step 17.2: Implement `POST /api/launch/exchange`**

Extend `backend/app/api/launchers.py`:

```python
from app.config import CONFIG
from app.models.launchers import ExchangePayload, ExchangeRequest, ExchangeResponse


@router.post("/launch/exchange", response_model=ExchangeResponse)
def post_exchange(req: ExchangeRequest, request: Request) -> ExchangeResponse:
    _check_flag()
    consumed = codes_repo.consume(req.code)
    if consumed is None:
        raise HTTPException(status_code=404, detail="unknown launch code")
    if consumed == "already_consumed":
        raise HTTPException(status_code=409, detail="launch code already consumed")
    if consumed == "expired":
        raise HTTPException(status_code=410, detail="launch code expired")
    assert isinstance(consumed, dict)

    sess = sessions_repo.get(consumed["agent_session_id"])
    assert sess is not None
    manifest = get_registry().get(sess["tool_id"])
    if manifest is None:
        raise HTTPException(status_code=500, detail="tool_id no longer recognized")

    sessions_repo.mark_active(sess["id"], machine_id=req.machine_id)

    raw_token = _resolve_raw_token(consumed["mcp_token_id"])
    endpoint = str(request.base_url).rstrip("/") + "/api/mcp"

    is_resume = sess["cli_session_id"] is not None
    payload = ExchangePayload(
        session_id=sess["id"],
        working_dir=sess["working_dir"],
        first_turn_prompt=None if is_resume else sess["first_turn_prompt"],
        cli_session_id=sess["cli_session_id"] if is_resume else None,
    )

    return ExchangeResponse(
        mcp_token=raw_token,
        endpoint=endpoint,
        manifest=manifest.model_dump(mode="json", exclude_none=True),
        payload=payload,
    )


def _resolve_raw_token(token_id: str) -> str:
    """Look up the raw token plaintext.

    NOTE: today's ``mcp_tokens`` table only stores ``token_hash``. The
    raw plaintext is only available at creation time. For launcher tokens,
    we'd ideally store an encrypted plaintext side-table — but Phase 1 is
    a spec/scaffold, so we mint a fresh token here and return it. Phase 2
    will introduce a launcher-token table that holds the encrypted value.

    For Phase 1, regenerate by minting a new token row. See P2 #8 in the
    design doc for the proper launcher-token lifecycle.
    """
    # TODO(Phase 2 P2#8): replace with proper launcher-token table.
    raise NotImplementedError(
        "raw-token lookup requires launcher_token table — see design.md P2 #8"
    )
```

**Important:** This `_resolve_raw_token` placeholder will fail the exchange tests as written. Instead of minting + storing a fresh token in `mcp_tokens` (which would shed plaintext), Phase 1 introduces a SMALL companion table to hold the encrypted launcher token plaintext. Add it now:

- [ ] **Step 17.3: Add `LauncherToken` model + migration update**

This is a Phase-1 scope adjustment that resolves the raw-token availability problem cleanly. Add to `backend/app/db/models.py` (after `LaunchCode`):

```python
class LauncherToken(Base):
    """Plaintext-bearing companion to ``mcp_tokens`` for launcher use.

    The regular ``mcp_tokens`` table stores only ``token_hash``, so the
    helper cannot retrieve the plaintext after creation. For
    launcher-minted tokens we keep an encrypted plaintext here.

    Encryption: AES-GCM with key from ``CONFIG.secret_key``. Token is
    short-lived (revoked on session close — see P2 #8). Decryption
    happens server-side during exchange.
    """
    __tablename__ = "launcher_tokens"

    mcp_token_id: Mapped[str] = mapped_column(
        Text, ForeignKey("mcp_tokens.id", ondelete="CASCADE"), primary_key=True,
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=_NOW_TEXT_DEFAULT)
```

Add `LargeBinary` to the existing models.py imports if not already there.

Then extend the migration. Open `backend/app/db/migrations/versions/0014_launchers.py` and add after the `page_working_dirs` block:

```python
    if not inspector.has_table("launcher_tokens"):
        op.create_table(
            "launcher_tokens",
            sa.Column(
                "mcp_token_id",
                sa.Text(),
                sa.ForeignKey("mcp_tokens.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("nonce", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
        )
```

And in `downgrade()`, add a corresponding `op.drop_table("launcher_tokens")` at the top.

- [ ] **Step 17.4: Implement the encrypt/decrypt helper + auto-mint with storage**

Create `backend/app/launchers/launcher_tokens.py`:

```python
"""Plaintext-bearing companion to ``mcp_tokens`` for launcher-minted tokens.

The helper needs the raw bearer to pass to claude/codex. Regular MCP
tokens only store a bcrypt hash; we keep the plaintext for these
specific tokens, AES-GCM encrypted with the app secret key.

Tokens stored here are scoped to launcher use — Phase 2 will add a
lifecycle (TTL, revoke-on-close). For now the same plaintext is
reused across exchanges for the same user.
"""
from __future__ import annotations

import secrets
from base64 import b64decode, b64encode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.auth import mcp_tokens as tokens_repo
from app.config import CONFIG
from app.db.models import LauncherToken
from app.db.session import session


def _key() -> bytes:
    # Derive a 32-byte key from the app secret. SECRET_KEY is already
    # required at boot, so this can't be missing.
    import hashlib
    return hashlib.sha256(CONFIG.secret_key.encode("utf-8")).digest()


def get_or_mint_for_user(user_id: str, *, name: str) -> tuple[str, str]:
    """Return ``(token_id, raw_token)``. If the user already has a
    launcher token, return it; otherwise mint a fresh one and store
    the encrypted plaintext.
    """
    with session() as s:
        # See if a launcher_tokens row exists for this user.
        existing = s.execute(
            select(LauncherToken).join(
                tokens_repo.McpToken,  # type: ignore[attr-defined]
                LauncherToken.mcp_token_id == tokens_repo.McpToken.id,  # type: ignore[attr-defined]
            ).where(
                tokens_repo.McpToken.user_id == user_id,  # type: ignore[attr-defined]
            )
        ).first()
        if existing is not None:
            row = existing[0]
            aesgcm = AESGCM(_key())
            raw = aesgcm.decrypt(row.nonce, row.ciphertext, None).decode("utf-8")
            return row.mcp_token_id, raw

    # Mint fresh.
    token_id, raw = tokens_repo.create(user_id, name)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, raw.encode("utf-8"), None)
    with session() as s:
        s.add(LauncherToken(mcp_token_id=token_id, ciphertext=ciphertext, nonce=nonce))
    return token_id, raw


def get_raw_for_token_id(mcp_token_id: str) -> str | None:
    with session() as s:
        row = s.get(LauncherToken, mcp_token_id)
        if row is None:
            return None
        return AESGCM(_key()).decrypt(row.nonce, row.ciphertext, None).decode("utf-8")
```

(Direct ORM imports from `tokens_repo.McpToken` won't work; import `McpToken` from `app.db.models` directly in this file. Fix as part of writing it.)

- [ ] **Step 17.5: Wire `_resolve_raw_token` + `post_launch` auto-mint to use it**

In `backend/app/api/launchers.py`, replace the placeholder `_resolve_raw_token`:

```python
from app.launchers import launcher_tokens

def _resolve_raw_token(token_id: str) -> str:
    raw = launcher_tokens.get_raw_for_token_id(token_id)
    if raw is None:
        raise HTTPException(status_code=500, detail="launcher token plaintext missing")
    return raw
```

In `post_launch`, replace the "auto-mint a launcher MCP token" block with:

```python
    token_id, _ = launcher_tokens.get_or_mint_for_user(user.id, name=f"launcher-{req.tool_id}")
```

- [ ] **Step 17.6: Verify `cryptography` is a dependency**

Check `backend/pyproject.toml` includes `cryptography`. If not, add it under `[project] dependencies`:

```toml
"cryptography>=42.0.0"
```

Then `cd backend && uv sync --extra dev`.

- [ ] **Step 17.7: Run tests**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_api.py -v
```

Expected: all passing.

- [ ] **Step 17.8: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/db/models.py backend/app/db/migrations/versions/0014_launchers.py backend/app/launchers/launcher_tokens.py backend/app/api/launchers.py backend/tests/test_launch_api.py backend/pyproject.toml
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): POST /api/launch/exchange + encrypted launcher_tokens table"
```

---

## Task 18: Probe-ack / probe-status endpoints

**Files:**

- Modify: `backend/app/api/launchers.py`
- Modify: `backend/tests/test_launch_api.py`

These two endpoints let the frontend detect "is the helper installed on this machine" via a hidden iframe → helper POSTs ack → frontend polls status.

- [ ] **Step 18.1: Append tests**

```python
def test_probe_ack_then_status(client):
    fresh = TestClient(create_app())
    nonce = "test_nonce_123"
    fresh.post("/api/launch/probe-ack", json={"nonce": nonce, "helper_port": 31415})
    res = fresh.get(f"/api/launch/probe-status?nonce={nonce}")
    assert res.status_code == 200
    body = res.json()
    assert body["acked"] is True
    assert body["helper_port"] == 31415


def test_probe_status_no_ack_returns_acked_false(client):
    res = client.get("/api/launch/probe-status?nonce=never_acked")
    assert res.status_code == 200
    assert res.json()["acked"] is False
```

- [ ] **Step 18.2: Implement (in-memory store; flushed per-process)**

Append to `backend/app/api/launchers.py`:

```python
from threading import RLock
from time import time

from app.models.launchers import ProbeAckRequest, ProbeStatusResponse

_PROBE_TTL = 5.0
_probe_store: dict[str, tuple[float, int]] = {}
_probe_lock = RLock()


def _gc_probes() -> None:
    now = time()
    with _probe_lock:
        stale = [n for n, (ts, _) in _probe_store.items() if now - ts > _PROBE_TTL]
        for n in stale:
            del _probe_store[n]


@router.post("/launch/probe-ack")
def post_probe_ack(req: ProbeAckRequest) -> dict[str, bool]:
    _check_flag()
    _gc_probes()
    with _probe_lock:
        _probe_store[req.nonce] = (time(), req.helper_port)
    return {"ok": True}


@router.get("/launch/probe-status", response_model=ProbeStatusResponse)
def get_probe_status(nonce: str) -> ProbeStatusResponse:
    _check_flag()
    _gc_probes()
    with _probe_lock:
        entry = _probe_store.get(nonce)
    if entry is None:
        return ProbeStatusResponse(acked=False, helper_port=None)
    _, port = entry
    return ProbeStatusResponse(acked=True, helper_port=port)
```

- [ ] **Step 18.3: Run tests**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_launch_api.py -v -k probe
```

Expected: 2 passed.

- [ ] **Step 18.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/api/launchers.py backend/tests/test_launch_api.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): probe-ack + probe-status endpoints"
```

---

## Task 19: `agent_sessions` router (list / heartbeat / cli-session / close)

**Files:**

- Create: `backend/app/api/agent_sessions.py`
- Test: `backend/tests/test_agent_sessions_api.py`

- [ ] **Step 19.1: Write the failing test**

Create `backend/tests/test_agent_sessions_api.py`:

```python
"""HTTP API for agent_sessions (list, heartbeat, cli-session, close)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db
from app.launchers import sessions as sessions_repo
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


def _seed_session(uid: str, sid: str = "as_1", **kw) -> str:
    return sessions_repo.create(
        user_id=uid,
        tool_id=kw.get("tool_id", "claude-code"),
        first_turn_prompt=kw.get("first_turn_prompt", "x"),
        wiki_path=kw.get("wiki_path"),
        working_dir=kw.get("working_dir"),
    )


def test_list_sessions_for_user(client):
    uid = seed_user()
    login_fastapi(client, uid)
    a = _seed_session(uid, wiki_path="match.md")
    b = _seed_session(uid, wiki_path="other.md")
    res = client.get("/api/agent-sessions")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()["sessions"]}
    assert ids == {a, b}


def test_list_sessions_filtered_by_wiki_path(client):
    uid = seed_user()
    login_fastapi(client, uid)
    a = _seed_session(uid, wiki_path="match.md")
    _seed_session(uid, wiki_path="other.md")
    res = client.get("/api/agent-sessions?wiki_path=match.md")
    assert {s["id"] for s in res.json()["sessions"]} == {a}


def test_list_sessions_only_returns_callers_own(client):
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    sid_a = _seed_session(a)
    _seed_session(b)
    login_fastapi(client, a)
    res = client.get("/api/agent-sessions")
    assert {s["id"] for s in res.json()["sessions"]} == {sid_a}


def test_heartbeat_updates_last_activity(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    before = sessions_repo.get(sid)["last_activity_at"]
    res = client.post(f"/api/agent-sessions/{sid}/heartbeat")
    assert res.status_code == 204
    after = sessions_repo.get(sid)["last_activity_at"]
    assert after >= before


def test_heartbeat_cross_user_forbidden(client):
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    sid = _seed_session(a)
    login_fastapi(client, b)
    res = client.post(f"/api/agent-sessions/{sid}/heartbeat")
    assert res.status_code == 403


def test_cli_session_id_post(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    res = client.post(f"/api/agent-sessions/{sid}/cli-session", json={
        "cli_session_id": "cli_xyz",
    })
    assert res.status_code == 204
    assert sessions_repo.get(sid)["cli_session_id"] == "cli_xyz"


def test_close(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    res = client.post(f"/api/agent-sessions/{sid}/close", json={"reason": "user_clicked"})
    assert res.status_code == 204
    row = sessions_repo.get(sid)
    assert row["status"] == "closed"
```

Run, expect FAIL.

- [ ] **Step 19.2: Write the router**

Create `backend/app/api/agent_sessions.py`:

```python
"""HTTP API for ``agent_sessions``.

Routes mounted under ``/api/agent-sessions`` from
``app.main:create_app``. All routes gated by ``CONFIG.launchers_enabled``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import User
from app.auth.deps import require_user
from app.config import CONFIG
from app.launchers import sessions as sessions_repo
from app.models.launchers import (
    AgentSessionList,
    AgentSessionSummary,
    CliSessionUpdateRequest,
    CloseRequest,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _check_flag() -> None:
    if not CONFIG.launchers_enabled:
        raise HTTPException(status_code=404, detail="launchers disabled")


def _require_own_session(sid: str, user: User) -> dict:
    row = sessions_repo.get(sid)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="not your session")
    return row


@router.get("", response_model=AgentSessionList)
def list_sessions(
    wiki_path: str | None = None, user: User = Depends(require_user),
) -> AgentSessionList:
    _check_flag()
    if wiki_path is not None:
        rows = sessions_repo.list_for_page(user_id=user.id, wiki_path=wiki_path)
    else:
        rows = sessions_repo.list_for_user(user.id)
    return AgentSessionList(sessions=[
        AgentSessionSummary(
            id=r["id"], tool_id=r["tool_id"], wiki_path=r["wiki_path"],
            working_dir=r["working_dir"], status=r["status"],
            started_at=r["started_at"], last_activity_at=r["last_activity_at"],
            closed_at=r["closed_at"], cli_session_id=r["cli_session_id"],
        ) for r in rows
    ])


@router.post("/{sid}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(sid: str, user: User = Depends(require_user)) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.touch_activity(sid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sid}/cli-session", status_code=status.HTTP_204_NO_CONTENT)
def set_cli_session(
    sid: str, req: CliSessionUpdateRequest, user: User = Depends(require_user),
) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.set_cli_session_id(sid, req.cli_session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sid}/close", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    sid: str, req: CloseRequest, user: User = Depends(require_user),
) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.close(sid, reason=req.reason or "user_clicked")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 19.3: Register router**

In `backend/app/main.py`, add:

```python
    from app.api import agent_sessions as agent_sessions_router
    app.include_router(agent_sessions_router.router, prefix="/api/agent-sessions")
```

- [ ] **Step 19.4: Run tests**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_agent_sessions_api.py -v
```

Expected: 7 passed.

- [ ] **Step 19.5: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/api/agent_sessions.py backend/app/main.py backend/tests/test_agent_sessions_api.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): agent_sessions API (list, heartbeat, cli-session, close)"
```

---

## Task 20: `expire_launch_artifacts` task on lightweight queue

**Files:**

- Create: `backend/app/tasks/expire_launch_artifacts.py`
- Modify: `backend/app/tasks/run_worker.py` (import the new module)
- Test: `backend/tests/test_expire_launch_artifacts.py`

- [ ] **Step 20.1: Write the failing test**

Create `backend/tests/test_expire_launch_artifacts.py`:

```python
"""Periodic sweep — expire launch codes + transition stale sessions."""
from __future__ import annotations

import pytest

from app.auth import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db
from app.launchers import sessions as sessions_repo
from app.tasks.expire_launch_artifacts import expire_launch_artifacts
from tests._seed import seed_user


def test_sweep_runs_without_errors(tmp_config):
    init_db()
    # No-op sweep on empty DB.
    expire_launch_artifacts()


def test_sweep_deletes_expired_codes(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    tid, _ = tokens_repo.create(uid, "k")
    monkeypatch.setattr(codes_repo, "_TTL_SECONDS", 0)
    codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)

    expire_launch_artifacts()
    assert codes_repo.expire_sweep() == 0  # already swept


def test_sweep_marks_stale_active_as_idle(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    monkeypatch.setattr(sessions_repo, "_IDLE_SECONDS", 0)
    expire_launch_artifacts()
    assert sessions_repo.get(sid)["status"] == "idle"
```

- [ ] **Step 20.2: Write the task**

Create `backend/app/tasks/expire_launch_artifacts.py`:

```python
"""Periodic sweep for launch codes + stale agent sessions.

Runs on ``lightweight_maintenance_queue`` per the placement rule —
sub-second, no LLM, no external HTTP, no wiki commits.
"""
from __future__ import annotations

import logging

from app.auth import launch_codes as codes_repo
from app.launchers import sessions as sessions_repo
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.periodic_task(crontab(minute="*"))
def expire_launch_artifacts() -> None:
    deleted = codes_repo.expire_sweep()
    idle = sessions_repo.mark_stale_idle()
    closed = sessions_repo.evict_idle_to_closed()
    if deleted or idle or closed:
        log.info(
            "expire_launch_artifacts deleted=%d marked_idle=%d closed=%d",
            deleted, idle, closed,
        )
```

- [ ] **Step 20.3: Register module in `run_worker.py`**

Open `backend/app/tasks/run_worker.py`. Find the line importing task modules:

```python
from app.tasks import agent_activity, chat_title, document_update, periodic, reindex, triggers  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

Replace with:

```python
from app.tasks import (  # noqa: F401  # pyright: ignore[reportUnusedImport]
    agent_activity,
    chat_title,
    document_update,
    expire_launch_artifacts,
    periodic,
    reindex,
    triggers,
)
```

- [ ] **Step 20.4: Run test**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_expire_launch_artifacts.py -v
```

Expected: 3 passed.

- [ ] **Step 20.5: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/tasks/expire_launch_artifacts.py backend/app/tasks/run_worker.py backend/tests/test_expire_launch_artifacts.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): expire_launch_artifacts task on lightweight queue"
```

---

## Task 21: MCP server `X-Agentwiki-Session` header threading + cross-user 403

**Files:**

- Modify: `backend/app/api/mcp_server.py`
- Modify: `backend/app/wiki/agent_activity.py:147` (`upsert_activity` accepts `agent_session_id`)
- Modify: `backend/app/llm/agents/tools/_doc_helpers.py` (thread the value through to the activity registry; needs investigation of `_doc_helpers`)
- Test: `backend/tests/test_mcp_session_stamp.py`

This is the most invasive task — it stitches together the launcher session with the existing MCP activity-stamping pipeline.

- [ ] **Step 21.1: Inspect `_doc_helpers` and `agent_activity.upsert_activity`**

```bash
cd /Users/nikolas/agent-wiki/backend
grep -n "upsert_activity\|agent_session_id" app/llm/agents/tools/_doc_helpers.py app/wiki/agent_activity.py app/wiki/notify.py
```

Read the matched code. The path you need to thread `agent_session_id` through is:

`api/mcp_server.py` reads header → store in ContextVar (`current_agent_session_ctx`) → `_doc_helpers.commit_and_fan_out` reads from ContextVar → passes to `agent_activity.upsert_activity` → writes to `agent_activity.agent_session_id` column.

- [ ] **Step 21.2: Add ContextVar for agent session id**

Create `backend/app/launchers/current_session.py`:

```python
"""ContextVar for the agent session id stamped on MCP activity rows.

The MCP server middleware reads ``X-Agentwiki-Session`` and binds the
id here; the wiki commit helpers read it to stamp
``agent_activity.agent_session_id``.

Mirrors the pattern used by ``app.auth.current_user`` /
``current_user_ctx``.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

current_agent_session_id_ctx: ContextVar[str | None] = ContextVar(
    "current_agent_session_id", default=None,
)


def current_agent_session_id() -> str | None:
    return current_agent_session_id_ctx.get()


@contextmanager
def set_current_agent_session_id(sid: str | None) -> Iterator[None]:
    token = current_agent_session_id_ctx.set(sid)
    try:
        yield
    finally:
        current_agent_session_id_ctx.reset(token)
```

- [ ] **Step 21.3: Update `upsert_activity` to accept and persist `agent_session_id`**

Open `backend/app/wiki/agent_activity.py`. Find `upsert_activity` (around line 147). Add `agent_session_id: str | None = None` to its kwargs and pass it through to the `AgentActivity(...)` constructor + the `existing.<...>` update branch:

```python
def upsert_activity(
    *,
    user_id: str,
    agent_name: str | None,
    doc_path: str,
    activity: str,
    description: str | None,
    ttl: timedelta = DEFAULT_TTL,
    agent_session_id: str | None = None,
) -> str:
    # ... existing body ...
        if existing is not None:
            existing.doc_path = doc_path
            existing.activity = activity
            existing.description = description
            existing.registered_at = registered_at
            existing.expires_at = expires_at
            existing.agent_session_id = agent_session_id
        else:
            s.add(
                AgentActivity(
                    user_id=user_id,
                    agent_name=agent_name,
                    doc_path=doc_path,
                    activity=activity,
                    description=description,
                    registered_at=registered_at,
                    expires_at=expires_at,
                    agent_session_id=agent_session_id,
                )
            )
```

- [ ] **Step 21.4: Thread the value through the commit pipeline**

Find the call site in `backend/app/llm/agents/tools/_doc_helpers.py` (or wherever `upsert_activity` is called from MCP-driven writes). Update the call to include:

```python
from app.launchers.current_session import current_agent_session_id

# ... inside the function that calls upsert_activity ...
agent_session_id = current_agent_session_id()
agent_activity.upsert_activity(
    # ... existing kwargs ...
    agent_session_id=agent_session_id,
)
```

- [ ] **Step 21.5: Read header in MCP server router + cross-user 403**

Open `backend/app/api/mcp_server.py`. After the `with set_current_user(user):` line in `transport_post`, layer in the agent-session ContextVar:

```python
from app.launchers import sessions as sessions_repo
from app.launchers.current_session import set_current_agent_session_id

AGENT_SESSION_HEADER = "X-Agentwiki-Session"


def _resolve_agent_session_id(request: Request, user: User) -> str | None:
    header = request.headers.get(AGENT_SESSION_HEADER)
    if not header:
        return None
    row = sessions_repo.get(header)
    if row is None:
        raise HTTPException(status_code=400, detail="unknown agent session id")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="session does not belong to this user")
    sessions_repo.touch_activity(header)
    return header


# In transport_post:
async def transport_post(...) -> Response:
    body: dict[str, Any] = rpc.model_dump(exclude_unset=True)
    incoming = request.headers.get(SESSION_HEADER)
    agent_session_id = _resolve_agent_session_id(request, user)

    with set_current_user(user), set_current_agent_session_id(agent_session_id):
        response, outgoing = dispatch(body, incoming, user)
    # ... rest unchanged ...
```

- [ ] **Step 21.6: Write the test**

Create `backend/tests/test_mcp_session_stamp.py`:

```python
"""``X-Agentwiki-Session`` stamps activity rows; cross-user → 403."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db, session
from app.db.models import AgentActivity
from app.launchers import sessions as sessions_repo
from app.main import create_app
from app.mcp_server import session as mcp_session
from sqlalchemy import select

from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


def _handshake(client, raw: str) -> dict[str, str]:
    auth = {"Authorization": f"Bearer {raw}"}
    res = client.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    }, headers=auth)
    sess_id = res.headers["Mcp-Session-Id"]
    client.post("/api/mcp", json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, headers={**auth, "Mcp-Session-Id": sess_id})
    return {**auth, "Mcp-Session-Id": sess_id}


def test_header_stamps_agent_session_id(client, tmp_repo):
    """``X-Agentwiki-Session`` causes the resulting activity row to
    carry ``agent_session_id``. ``read_doc`` stamps a ``read`` activity
    on success per the existing chat-tool registry."""
    from app.wiki import git as wiki_git
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    wiki_git.commit_file("x.md", "# Hello\n", actor=uid, message="seed")
    agent_sid = sessions_repo.create(
        user_id=uid, tool_id="claude-code", first_turn_prompt="x",
        wiki_path="x.md", working_dir=None,
    )
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = agent_sid

    client.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "read_doc", "arguments": {"path": "x.md"}},
    }, headers=h)

    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    assert any(a.agent_session_id == agent_sid and a.doc_path == "x.md" for a in rows)


def test_unknown_session_id_returns_400(client):
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = "as_does_not_exist"
    res = client.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 99, "method": "ping",
    }, headers=h)
    assert res.status_code == 400


def test_cross_user_session_returns_403(client):
    """P1 / P2 #7 — bearer holder can't stamp another user's session."""
    init_db()
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    _, raw_b = tokens_repo.create(b, "k")
    sid_a = sessions_repo.create(
        user_id=a, tool_id="claude-code", first_turn_prompt="x",
        wiki_path=None, working_dir=None,
    )
    h = _handshake(client, raw_b)
    h["X-Agentwiki-Session"] = sid_a
    res = client.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 99, "method": "ping",
    }, headers=h)
    assert res.status_code == 403
```

- [ ] **Step 21.7: Run tests**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_mcp_session_stamp.py -v
```

Expected: 3 passed.

- [ ] **Step 21.8: Run the broader MCP suite to ensure no regressions**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/test_mcp_server_tools.py tests/test_mcp_server_writes.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 21.9: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/current_session.py backend/app/api/mcp_server.py backend/app/wiki/agent_activity.py backend/app/llm/agents/tools/_doc_helpers.py backend/tests/test_mcp_session_stamp.py
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): X-Agentwiki-Session header threading + cross-user 403"
```

---

## Task 22: End-to-end integration test

**Files:**

- Create: `backend/tests/integration/test_launch_e2e.py`

- [ ] **Step 22.1: Write the integration test**

Create `backend/tests/integration/test_launch_e2e.py`:

```python
"""End-to-end launch → exchange → MCP call → activity stamp.

No real CLI spawn; we fake the helper by directly calling
``POST /api/launch/exchange`` with the launch code from the wizard
response, then issuing an MCP tools/call with the returned bearer +
session header.

This proves the full backend pipeline is correctly wired without
needing the npm helper.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db, session
from app.db.models import AgentActivity
from app.main import create_app
from app.mcp_server import session as mcp_session
from sqlalchemy import select

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    init_db()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


def _seed_page(repo_dir, rel: str, body: str) -> str:
    from app.wiki import git as wiki_git
    return wiki_git.commit_file(rel, body, actor="seed", message="seed")


def test_full_launch_flow(client, tmp_repo):
    uid = seed_user()
    login_fastapi(client, uid)
    _seed_page(tmp_repo, "x.md", "# Hello\n\nbody.")

    # 1. POST /api/launch
    res = client.post("/api/launch", json={
        "tool_id": "claude-code", "wiki_path": "x.md", "message": "go",
    })
    assert res.status_code == 200
    body = res.json()
    code = body["launch_code"]
    agent_sid = body["agent_session_id"]

    # 2. POST /api/launch/exchange (helper-facing — no cookie)
    fresh = TestClient(create_app())
    ex = fresh.post("/api/launch/exchange", json={
        "code": code, "machine_id": "m_e2e",
    })
    assert ex.status_code == 200
    exb = ex.json()
    raw_token = exb["mcp_token"]
    assert exb["payload"]["first_turn_prompt"] is not None
    assert "Hello" in exb["payload"]["first_turn_prompt"]  # page body inlined

    # 3. Fake helper: open MCP with the bearer + session header.
    auth = {"Authorization": f"Bearer {raw_token}"}
    init_res = fresh.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    }, headers=auth)
    sess_id = init_res.headers["Mcp-Session-Id"]
    headers = {
        **auth,
        "Mcp-Session-Id": sess_id,
        "X-Agentwiki-Session": agent_sid,
    }
    fresh.post("/api/mcp", json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, headers=headers)

    # 4. Issue a tool call (read_doc) — should stamp an activity row.
    fresh.post("/api/mcp", json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "read_doc", "arguments": {"path": "x.md"}},
    }, headers=headers)

    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    assert any(a.agent_session_id == agent_sid and a.doc_path == "x.md" for a in rows)
```

- [ ] **Step 22.2: Run**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest tests/integration/test_launch_e2e.py -v
```

Expected: 1 passed.

- [ ] **Step 22.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add backend/tests/integration/test_launch_e2e.py
git -C /Users/nikolas/agent-wiki commit -m "test(launchers): integration test for launch → exchange → MCP → activity"
```

---

## Task 23: Run the full test suite + push

- [ ] **Step 23.1: Full suite**

```bash
cd /Users/nikolas/agent-wiki/backend
uv run --extra dev pytest -x
```

Expected: all green. If any pre-existing test fails because of our changes, fix the regression before continuing.

- [ ] **Step 23.2: Pre-commit on changed files**

```bash
cd /Users/nikolas/agent-wiki
pre-commit run --files $(git diff --name-only main...HEAD)
```

Expected: all pass.

- [ ] **Step 23.3: Push**

```bash
git -C /Users/nikolas/agent-wiki push
```

PR #27 is auto-updated.

---

## Done

After Task 23, Phase 1 backend is shippable:

- All new tables migrate cleanly.
- API surface gated by `LAUNCHERS_ENABLED=false` in production (test conftest sets it to true).
- Full test coverage via `TestClient` + per-test Postgres schema.
- MCP server threads `X-Agentwiki-Session` with cross-user 403.
- No frontend, no helper — those land in Phase 2 and Phase 3.

Next plans land in this directory as:

- `phase_2_frontend_wizard.md`
- `phase_3_mac_helper.md`
- `phase_4_cross_os_helper.md`
