# Infra

> **Part of agent-workspace v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns deployment, container layout,
> volumes, env, observability, and the operational concerns of running
> agent-workspace. Code-level architecture lives in the other per-area
> docs (e.g. [flask-and-apis](../flask-and-apis/flask-and-apis.md)).

_Last updated: 2026-05-06_

---

## Design

### Container layout (current)

```
nginx :80                 reverse proxy: /api/* → backend, else → frontend
  ├── backend :8080       Flask app
  ├── worker              Huey consumer (same image, different command)
  └── frontend :3000      Next.js standalone
```

All four services are defined in `docker-compose.yml` at the repo root.
Compose is the only wiring today — no Helm chart, no k8s manifests yet.

### Volumes

| Volume | Mount | Purpose |
|---|---|---|
| `app-data`  | `/data`  | `app.sqlite` + `queue.sqlite` |
| `wiki-data` | `/wiki`  | git-backed wiki working tree (per V0 brief) |

**Two separate volumes by design:** the SQLite store can be backed by
fast block storage; the wiki repo can live on a slower (or
network-mounted, replicated, snapshotted) volume since it's the durable
content store.

### Env model

Runtime config split:
- **Static / boot-time** — env vars (`SECRET_KEY`, `WIKI_DIR`,
  `*_DB_PATH`, `AUTH_MODE`, `ALLOWED_EMAILS`, OIDC settings).
- **Mutable / admin-managed** — DB rows (LLM provider/model/keys via
  `llm_settings`).

`.env.example` lists the env knobs. **No LLM keys in env** — they live in
the DB and are configured at runtime via the admin UI.

### Auth secrets
- `SECRET_KEY` — Flask session signing. Must be set in production; default
  is `dev-secret`.
- bcrypt-hashed user passwords in `users.password_hash`. Hashes are not
  redacted; never log row contents.
- LLM API keys redacted in admin GET responses (`abcd…wxyz` form), full
  values only sent in PUT bodies.

### Observability (minimal today)
- Python logging at INFO via `logging.basicConfig` in `app/main.py`.
- Audit log of system events lives in the `events` table (kind +
  payload). This is the durable audit trail, not just logs.
- No metrics / tracing yet.

### Boot order considerations
- `init_db()` runs migrations on every backend boot; idempotent.
- `ensure_wiki_repo()` initializes the git repo if missing; sets the
  hardcoded identity `agent-workspace@local`.
- The worker container shares both volumes and runs migrations
  implicitly via `app.tasks.huey_app` import (not strictly necessary
  today, but cheap).

### Backups
Not implemented. When this matters:
- `app.sqlite` — periodic `VACUUM INTO` snapshot to the wiki volume or
  external storage. WAL mode is on; mid-write copies are fine.
- `queue.sqlite` — recoverable; tasks can be re-driven from the events
  log.
- `wiki-data` — push a mirror to a remote git remote on a cron. The wiki
  *is* a git repo; this is the cleanest backup story.

### Production deltas (not done yet)
- `SESSION_COOKIE_SECURE = True` behind HTTPS (currently False; the
  comment in `main.py` flags this).
- Real `SECRET_KEY` from a secret store, not env.
- Nginx terminating TLS (or a load balancer in front).
- A health check that exercises `init_db()` having run and a sample LLM
  ping (optional).

---

## Progress

### Working
- `docker-compose.yml` boots all four containers locally.
- Volumes wired correctly; data persists across restarts.
- Migrations run on boot; FTS5 active.
- Wiki repo auto-initializes.
- Worker registers tasks on import.

### Stubbed / partial
- `nginx.conf` is minimal — fine for local, may need tuning for prod
  (timeouts, gzip, body-size limits, websocket forwarding when streaming
  lands).
- Frontend Dockerfile uses `next start` against the Next standalone
  output — works but unverified at production load.

### Not started
- TLS / production hosting story.
- Backup automation.
- Metrics / tracing.
- Healthcheck endpoint that exercises more than a static `{status: ok}`.
- Per-deploy migration safety (rollback path if a migration breaks).

### Next up (concrete work units)
1. **`/api/health` improvement** — verify DB reachable, queue path
   writable, optionally pull an LLM-settings row count. Keep it cheap.
2. **`SESSION_COOKIE_SECURE` toggle** via env (`SECURE_COOKIES=true`).
3. **TLS docs** — even just a sample compose + `caddy` sidecar in
   `deploy/` so people don't roll their own.
4. **Backup recipe** — short doc covering: cron `git push --mirror` for
   the wiki, `VACUUM INTO` for SQLite.

### Open questions
- Where will this actually run for the first dogfood — a single VM, a
  managed-container product (Fly/Render), or k8s? Affects how much we
  invest in compose vs. moving to Helm/Kustomize. V0 brief just says
  "deployment"; pick after the first real install.
- Multi-worker concurrency on the wiki repo (lock strategy) — flagged
  in `background-tasks/background-tasks.md`. Becomes a real question if
  a single worker can't keep up.
