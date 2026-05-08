# Infra

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns deployment, container layout,
> volumes, env, observability, and the operational concerns of running
> agent-wiki. Code-level architecture lives in the other per-area
> docs (e.g. [flask-and-apis](../flask-and-apis/flask-and-apis.md)).

_Last updated: 2026-05-07_

---

## Design

### Container layout (current)

```
nginx :80                 reverse proxy: /api/* → backend, else → frontend
  ├── backend :8080       Flask app
  ├── worker-documents    Huey consumer for documents_huey
  ├── worker-triggers     Huey consumer for triggers_huey
  ├── worker-wiki-bm25    Huey consumer for wiki_bm25_huey
  └── frontend :3000      Next.js standalone
```

All three workers are the same image; the queue name is a positional
arg. Queue rationale + status live in
[background-tasks](../background-tasks/background-tasks.md).

All four services are defined in `docker-compose.yml` at the repo root.
Compose is the canonical local path. A k8s/EKS deploy story also lives in
`deploy/` — see "Production deploy (EKS + Helm)" below.

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
  hardcoded identity `agent-wiki@local`.
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

### Production deploy (EKS + Helm)

`deploy/` holds the production wiring. Two layers:

- **`deploy/terraform/`** — provisions VPC + EKS (using
  `terraform-aws-modules/{vpc,eks}/aws`) with the EBS CSI add-on, a `gp3`
  default StorageClass, ingress-nginx (NLB-backed), cert-manager, and an
  optional `letsencrypt-prod` ClusterIssuer (gated on `cert_manager_email`).
  State is local and gitignored.
- **`deploy/helm/agent-workspace/`** — chart with backend/worker/frontend
  Deployments, two PVCs (`app-data` 5Gi, `wiki-data` 10Gi, both `gp3`/RWO),
  a Secret for `SECRET_KEY` (+ optional OIDC client secret), and an Ingress
  that routes `/api/*` → backend and `/` → frontend (replacing the
  in-cluster nginx pod that compose uses).

Constraints baked into the chart:
- **Backend + worker pinned to 1 replica** with `strategy: Recreate` and a
  `podAffinity` rule that co-schedules them on one node — RWO PVCs +
  single-writer SQLite + single git working tree leave no room for HA at
  this layer.
- **Frontend** is stateless and free to scale.
- **No nginx pod** — ingress-nginx replaces it. Path routing lives in the
  Ingress resource; `proxy-body-size` matches the 25m the compose nginx had.

See `deploy/README.md` for the apply/install flow.

### Production deltas (still to do)
- `SESSION_COOKIE_SECURE = True` behind HTTPS — chart wires `SECURE_COOKIES`
  env from `values.yaml` (default `true`); backend code still needs to read
  it (`main.py` currently hard-codes `False`).
- Real `SECRET_KEY` from a secret store (External Secrets / AWS Secrets
  Manager) — chart currently takes it via `--set secretKey=...`.
- A health check that exercises `init_db()` and an LLM ping (optional).
- Backup automation (cron `git push --mirror` for the wiki PVC, `VACUUM INTO`
  for SQLite). Not wired.

---

## Progress

### Working
- `docker-compose.yml` boots all four containers locally.
- Volumes wired correctly; data persists across restarts.
- Migrations run on boot; FTS5 active.
- Wiki repo auto-initializes.
- Worker registers tasks on import.
- `deploy/terraform/` validates (`terraform validate` clean); `deploy/helm/`
  lints clean and templates against representative values. Not yet
  end-to-end applied against a real AWS account.

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
- Multi-worker concurrency on the wiki repo (lock strategy) — flagged
  in `background-tasks/background-tasks.md`. Becomes a real question if
  a single worker can't keep up; until then the helm chart pins worker
  replicas to 1 and co-schedules with the backend.
- First dogfood deploy: are we using the bundled `deploy/terraform` (own
  cluster) or installing the chart into the existing Onyx EKS? The chart
  works for either; Terraform is only needed for the former.
- Image registry — chart defaults assume a public GHCR/Docker Hub repo
  (no ECR provisioning). Revisit if private images become a requirement.
