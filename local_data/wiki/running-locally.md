# Running locally — agent guide

Quick reference for an agent (or human) running and debugging
agent-wiki on the host **without Docker**. The compose path in the
README is canonical; this is the fast-iteration alternative we actually use
day-to-day. See `architecture_and_progress.md` §3 "Local dev" for the
architectural context; this doc is the concrete runbook.

---

## Prereqs (already set up on this machine)

- Repo at `/Users/yuhongsun/Projects/agent-workspace`.
- Backend venv at `backend/.venv` (Python 3.11). Deps installed via
  `pip install -e .` from `backend/pyproject.toml`.
- `node_modules/` present in `frontend/`.
- `.env` at the repo root. Data paths point at `local_data/`:
  - `WIKI_DIR=…/local_data/wiki`
  - `APP_DB_PATH=…/local_data/app.sqlite`
  - `QUEUE_DB_PATH=…/local_data/queue.sqlite`
  - `BACKEND_URL=http://localhost:8080` — used by the Next dev-server
    rewrite to proxy `/api/*` to Flask.
- The wiki dir is a git repo; for files to show up in the UI they must be
  **tracked** (`/api/documents` is built from `git ls-files`). See the
  next section for setup details and recovery.

If you're starting from a fresh clone, see the bootstrap notes at the
bottom of this doc.

---

## Wiki dir — git requirements and setup

The wiki working tree (`WIKI_DIR`, default `local_data/wiki`) is **not** a
plain folder of markdown — the backend treats it as a real git repository
and shells out to `git` for every read/write. This shapes a few things you
need to get right.

### Why it has to be a git repo

- `app/wiki/git.py:list_paths` builds the directory listing from
  `git ls-files`. **Untracked files are invisible to the API**, even if they
  exist in the working tree.
- `app/wiki/git.py:read_file` reads via `git show <ref>:<path>`. Reading the
  current version requires that the file is committed at `HEAD`.
- `app/wiki/git.py:commit_file` is the only sanctioned write path. Every
  user/agent edit that goes through the UI does `git add` + `git commit`
  inside the wiki dir.
- `app/wiki/git.py:history` and the file-history view rely on `git log`
  for that path.

So the invariant is: *if it isn't tracked at HEAD, it doesn't exist as far
as the app is concerned*.

### What `ensure_wiki_repo()` does (and doesn't) on startup

Called from `create_app()` in `app/main.py`. Logic:

1. Ensure `WIKI_DIR` exists (mkdir -p).
2. If `.git/` already exists in `WIKI_DIR`, **return immediately** — no
   further setup happens.
3. Otherwise: `git init -b main`, set local `user.email` /
   `user.name`, then `git add -A` everything in the working tree and (only
   if there's something staged) `git commit -m "Seed wiki from working tree"`.

The seed step is gated on the absence of `.git/`. Concrete consequences:

- **Fresh dir, no `.git/`** → `ensure_wiki_repo()` inits and commits
  whatever was in the directory. Files show up immediately. ✅
- **Existing `.git/` with commits** → repo is valid; nothing to do. ✅
- **Existing `.git/` but *zero* commits** (e.g. someone init'd the repo on
  a previous run when the working tree was empty, then later dropped
  markdown files into the dir) → `ensure_wiki_repo()` short-circuits at
  step 2, the files stay untracked, the API returns an empty listing, and
  the UI looks empty. ⚠️ This is the failure mode we hit; see "First-run
  setup / recovery" below.

### First-run setup / recovery — make existing files appear in the UI

If you have markdown in `WIKI_DIR` that isn't showing up, do a one-time
bootstrap commit using the same identity the app uses:

```
cd "$WIKI_DIR"     # default: local_data/wiki
git init -b main 2>/dev/null || true     # no-op if already init'd
git config user.email agent-wiki@local
git config user.name agent-wiki
git add -A
git commit -m "Seed wiki from working tree"
```

After this, restart the backend (or just refresh the page; reads don't
cache the file list). Subsequent edits via the UI auto-commit through
`commit_file`.

### Seed content for a brand-new install

For a clean dev environment we have sample content under `wiki/seed/` in
the repo. To use it as the starting tree, copy it into `WIKI_DIR` *before*
the first backend start:

```
mkdir -p local_data/wiki
cp -R wiki/seed/. local_data/wiki/
```

Then start the backend; `ensure_wiki_repo()` will init the git repo and
commit the seed as the initial revision.

### Things to avoid

- Don't shell out to `git` against `WIKI_DIR` from anywhere in the backend
  *other than* `app/wiki/git.py`. (See CLAUDE.md.)
- Don't `git rm` files manually then forget to commit — the working tree
  diverges from `HEAD` and the API still won't show the change because
  `list_paths` reads the index, not the FS.
- Don't ignore wiki paths via `.gitignore` inside `WIKI_DIR` — anything
  ignored is, by construction, invisible to the app.
- Don't force-push or hard-reset the wiki repo. Wiki history is the
  authoritative trail of edits; only additive commits.

---

## How to run — three processes

All three are long-lived, so an agent should background them.

### 1. Backend (Flask, :8080)

```
cd backend
./.venv/bin/python -m app.main
```

`app/config.py` calls `dotenv.load_dotenv()` against the repo-root `.env`,
so no `source .env` is needed.

### 2. Worker (Huey)

```
cd backend
./.venv/bin/python -m app.tasks.run_worker
```

Same venv. Same dotenv auto-load.

### 3. Frontend (Next.js dev, :3000)

```
cd frontend
set -a && source ../.env && set +a && npm run dev
```

The `set -a / source` dance is required here because Next only auto-loads
`.env` from the frontend dir, not the repo root. Without it,
`BACKEND_URL` is undefined and the rewrite falls back to
`http://backend:8080` (a docker hostname that won't resolve on the host).

### Open at

http://localhost:3000 — **not** :8080. There is no nginx in this setup;
the Next dev server proxies `/api/*` to Flask via the rewrite in
`frontend/next.config.js`.

### Readiness check

```
curl -sf http://localhost:8080/api/health   # → {"status":"ok"}
curl -sf http://localhost:3000              # → 200 OK
```

### Stopping

```
lsof -ti:3000,8080 | xargs -r kill -9
```

---

## Where the logs live

### When a human runs the processes in their own terminals

Each process logs to stdout/stderr in the terminal where it was started.
Flask logs through `werkzeug` at INFO; Huey logs to stdout. Nothing is
written to a file by default.

### When an agent runs the processes via the Claude Code task harness

Each backgrounded `Bash(run_in_background: true)` invocation gets a
`task_id` (e.g. `bnz2gdll6`) and writes its merged stdout+stderr to:

```
/private/tmp/claude-501/-Users-yuhongsun-Projects-agent-workspace/<session-id>/tasks/<task-id>.output
```

The exact `<session-id>` is per-harness-session (a UUID) and is reported in
the bash task output when the task starts. `tail -f` or `tail -N` on that
file is the way to inspect what the process has emitted. The harness also
emits a notification when the task exits (with the exit code).

There is **no rotated log file** outside this. If you want persistent logs
across runs, redirect manually, e.g.:

```
./.venv/bin/python -m app.main > /tmp/agent-wiki-backend.log 2>&1
```

### SQLite databases (useful for poking at state)

- `local_data/app.sqlite` — users, documents, triggers cache, events,
  FTS5, llm_settings, _migrations.
- `local_data/queue.sqlite` — Huey task queue.

Open with `sqlite3 local_data/app.sqlite` for ad-hoc inspection.

### Wiki git history

The wiki working tree lives at `local_data/wiki/`. It is a real git repo;
`git -C local_data/wiki log --oneline` shows the commit history written by
the app's `commit_file` calls.

---

## Debugging recipes

### Backend won't start / port already in use

```
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -ti:8080 | xargs -r kill -9
```

Same for `:3000`.

### "Loading…" stuck on a wiki/chat page, 404s on `/_next/static/chunks/*`

The Next dev cache is corrupted (this happens after some kinds of restart).
Stop the dev server, blow away the cache, restart:

```
rm -rf frontend/.next
```

### Wiki page shows no files even though `local_data/wiki/` has markdown in it

Untracked files are invisible to the API (`/api/documents` is built from
`git ls-files`). See "Wiki dir — git requirements and setup" above for
the full picture and the one-time bootstrap commit recipe.

### Worker spamming `NotImplementedError` from `evaluate_scheduled_triggers`

Known stub in `app/tasks/periodic.py:16`. Fires on a periodic schedule.
Not blocking anything in the request path — ignore until that task is
implemented.

### `/api/documents` returns `{"error":"unauthorized"}`

You're hitting it without a session cookie. Sign in via the UI; from
`curl`, you'd need to log in to `/api/auth/login` first and pass the
cookie.

### Backend changes aren't picked up

The Flask dev server here runs without `debug=True`, so it does **not**
auto-reload. Restart the backend after editing Python code.

### Frontend changes aren't picked up

Next dev does hot-reload. If a change is mysteriously not reflecting,
check the dev-server log for compile errors, then `rm -rf frontend/.next`
as a last resort.

### Database schema looks stale

Migrations run on startup via `app/db/sqlite.py:init_db()`. If you added a
new file under `app/db/migrations/`, restart the backend. To re-bootstrap
from scratch: stop the app, `rm local_data/app.sqlite*` (and re-create the
admin user via the signup flow). Don't edit applied migrations — add a
new one.

---

## Bootstrap from a fresh clone

1. `cp .env.example .env`; set `SECRET_KEY` and adjust the local data
   paths to point under `local_data/` (or wherever you want them).
2. Backend venv:
   ```
   cd backend
   python3.11 -m venv .venv
   ./.venv/bin/pip install -e .
   ```
3. Frontend deps: `cd frontend && npm install`.
4. (Optional) Seed the wiki: `mkdir -p local_data/wiki && cp -R wiki/seed/. local_data/wiki/`. Do this **before** the first backend start so `ensure_wiki_repo()` commits the seed as the initial revision. See "Wiki dir — git requirements and setup" above for why this matters and what to do if you've already started the backend with an empty wiki dir.
5. Start the three processes per the section above. The first hit to the
   backend will create `local_data/app.sqlite`, run migrations, and
   `ensure_wiki_repo()` will init `local_data/wiki/` as a git repo.
6. Sign up at http://localhost:3000/signup — the first account is
   auto-promoted to admin.
7. In Admin → LLM, set provider/model/API key (env-var keys are only the
   pre-row fallback).

---

## Pointers into the codebase

- `backend/app/main.py` — Flask app factory; lists registered blueprints.
- `backend/app/config.py` — env loading.
- `backend/app/wiki/git.py` — only place that shells out to git.
- `backend/app/api/documents.py` — wiki listing/read endpoints.
- `backend/app/tasks/run_worker.py` — Huey worker entry.
- `frontend/next.config.js` — `/api/*` rewrite.
- `frontend/src/lib/api.ts` — `apiFetch` (the only allowed network call).
- `frontend/src/lib/auth.tsx` — auth context.

For deeper detail on any area, follow the per-area links in
`architecture_and_progress.md`.
