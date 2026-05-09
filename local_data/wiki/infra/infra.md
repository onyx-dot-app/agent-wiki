# Infra

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns deployment, container layout,
> volumes, env, observability, and the operational concerns of running
> agent-wiki. Code-level architecture lives in the other per-area
> docs (e.g. [flask-and-apis](../flask-and-apis/flask-and-apis.md)).

_Last updated: 2026-05-08_

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
- **Backend pinned to 1 replica** + **one Deployment per Huey queue** (one
  worker process for each of `documents`, `triggers`, `wiki_bm25`), all
  using `strategy: Recreate` and a `podAffinity` rule that co-schedules
  them on the backend's node — RWO PVCs + single-writer SQLite + single
  git working tree leave no room for HA at this layer.
- **Frontend** is stateless and free to scale.
- **No nginx pod** — ingress-nginx replaces it. Path routing lives in the
  Ingress resource; `proxy-body-size` matches the 25m the compose nginx had.
- **Single-AZ node group.** EBS volumes are AZ-bound; pinning the main node
  group to a single subnet (`slice(module.vpc.private_subnets, 0, 1)`)
  prevents cross-AZ node replacements from stranding the chart's RWO PVCs.
- **`t3.large` node default.** `t3.medium` maxes at 17 pods/node; cluster
  services + the chart's pods exhaust the budget and the worker can't
  schedule. `t3.large` supports 35.
- **Ingress-nginx via the AWS Load Balancer Controller** (not the legacy
  in-tree NLB), with `aws-load-balancer-type=external` +
  `nlb-target-type=ip` + `scheme=internet-facing`. The in-tree NLB doesn't
  open NodePort to `0.0.0.0/0` on the node SG and defaults to internal
  scheme — both make the LB unreachable from the public internet.

The chart is published as a Helm repo from `gh-pages` of this repo by
`.github/workflows/helm-release.yml`; consumers add it via
`helm repo add agent-wiki <gh-pages url>` and pin to a Chart.yaml version.

### Auth flows

Two `AUTH_MODE`s are wired end-to-end:
- **`basic`** — email + password signup/login, bcrypt-hashed in the `users`
  table. First account is auto-promoted to admin.
- **`oidc`** — Google (or any OIDC issuer) via authlib. `/api/auth/oidc/login`
  redirects to the IdP; `/api/auth/oidc/callback` exchanges the code,
  validates `email_verified` + the `ALLOWED_EMAILS` allow list, and upserts
  the user. First OIDC user is auto-admin same as basic. Frontend swaps the
  email/password form for a "Sign in with Google" button when
  `auth_config.mode == "oidc"`.

The team's own dogfood deploy runs in `oidc` mode locked to the org's email
domain via `allowedEmails`.

See `deploy/README.md` for the apply/install flow.

### Automated CI/CD

The dogfood deploy is fully automated. Two cron-driven workflows in two
repos form the chain:

1. **`agent-wiki:.github/workflows/nightly-build.yml`** — daily at 10 UTC
   and on-demand. Matrix-builds backend + frontend, multi-arch
   (`linux/amd64,linux/arm64`), pushes
   `onyxdotapp/agent-wiki-{backend,frontend}:nightly-latest-YYYYMMDD` to
   Docker Hub.
2. **A deploy workflow in the private cluster repo** — runs an hour later
   (11 UTC) and can be dispatched ad-hoc with a `version_tag` input.
   Probes Docker Hub for both images at the requested tag, assumes an
   IAM role via GitHub OIDC, pulls `SECRET_KEY` and the OIDC client
   secret from AWS Secrets Manager, runs `helm upgrade --install` with
   `--set image.{backend,frontend}.tag` + the secrets, and waits for
   rollout. Slack notifications on kickoff + result for ad-hoc runs;
   failure-only for scheduled.

Tag-driven `v*` builds (`docker-build-push.yml`) still exist for cutting a
named release; the automated path uses the date-rolled `nightly-latest-*`
tag instead so the deploy side doesn't have to chase a moving "latest".

**Ad-hoc deploy.** `ods deploy wiki` (shipped in the `onyx-devtools` PyPI
package) wraps the same chain end-to-end: dispatches the build workflow,
polls it to completion, then dispatches the deploy workflow with today's
tag. `--no-build` skips the rebuild and deploys whatever's already on
Docker Hub for the day's tag; `--no-wait-deploy` returns once the deploy
run starts. The deploy workflow itself can also be dispatched directly
from the cluster repo's Actions UI with an explicit `version_tag` to roll
back to a prior day.

### Production deltas (still to do)
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
- **End-to-end production deploy.** `deploy/terraform/` (template) +
  `deploy/helm/agent-workspace/` (published to gh-pages by chart-releaser)
  brought up a live EKS cluster with TLS via Let's Encrypt and Google OIDC
  sign-in. Backend + frontend + three queue workers all green.
- **`SECURE_COOKIES`** env wires `SESSION_COOKIE_SECURE` correctly behind
  HTTPS (no longer hard-coded `False`).
- **Image build/push** to Docker Hub (`onyxdotapp/agent-wiki-{backend,frontend}`)
  on `v*` tag in `agent-wiki:main` via `.github/workflows/docker-build-push.yml`.
  Multi-arch (`linux/amd64,linux/arm64`).
- **Nightly automated deploy.** `agent-wiki:.github/workflows/nightly-build.yml`
  cron-builds and pushes `:nightly-latest-YYYYMMDD` to Docker Hub at 10 UTC; a
  matching workflow in the private cluster repo runs an hour later and rolls
  the chart via `helm upgrade --install`, with secrets pulled from AWS Secrets
  Manager via GitHub OIDC. Both also support `workflow_dispatch` for ad-hoc
  runs, and `ods deploy wiki` (in `onyx-devtools` on PyPI) drives the chain
  from a single command. See "Automated CI/CD" above.

### Stubbed / partial
- `nginx.conf` is minimal — fine for local, may need tuning for prod
  (timeouts, gzip, body-size limits, websocket forwarding when streaming
  lands).
- Frontend Dockerfile uses `next start` against the Next standalone
  output — works but unverified at production load.

### Not started
- Backup automation.
- Metrics / tracing.
- Healthcheck endpoint that exercises more than a static `{status: ok}`.
- Per-deploy migration safety (rollback path if a migration breaks).

### Next up (concrete work units)
1. **`/api/health` improvement** — verify DB reachable, queue path
   writable, optionally pull an LLM-settings row count.
2. **Backup recipe** — short doc covering: cron `git push --mirror` for
   the wiki, `VACUUM INTO` for SQLite.

### Open questions
- Multi-worker concurrency on the wiki repo (lock strategy) — flagged
  in `background-tasks/background-tasks.md`. Becomes a real question if
  a single worker can't keep up; until then the helm chart pins worker
  replicas to 1 and co-schedules with the backend.
- Image registry — currently Docker Hub. Revisit if we want private images.
