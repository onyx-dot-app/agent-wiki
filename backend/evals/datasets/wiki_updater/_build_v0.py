"""Build the v0 wiki_updater eval dataset.

Run once after schema changes:

    cd backend && uv run python -m evals.datasets.wiki_updater._build_v0

Produces ``cases.jsonl`` next to this file. Hand-edits to the JSONL are
fine — this builder is the source of truth for v0 only, not a lock file.
"""

from __future__ import annotations

from pathlib import Path

from evals.schema import FactClaim, TriggerClass, WikiUpdaterCase


def _claim(cid: str, text: str) -> FactClaim:
    return FactClaim(id=cid, text=text)


def _process_no_change_cases() -> list[WikiUpdaterCase]:
    """Payload restates info already present — should return NO_CHANGE."""
    return [
        WikiUpdaterCase(
            id="pi-noc-01-runbook-already-current",
            surface="process_instruction",
            wiki_path="ops/runbooks/auth-outage.md",
            current_body=(
                "# Auth outage runbook\n\n"
                "## Symptom\n\n"
                "Users see 500s on /login. PagerDuty fires `auth-5xx-spike`.\n\n"
                "## First response\n\n"
                "1. Page the auth on-call.\n"
                "2. Check the `auth-api` dashboard for elevated 5xx.\n"
                "3. If error rate is over 5%, fail over to `auth-api-standby`.\n"
            ),
            payload={"instruction": "Make sure the runbook notes the PagerDuty alert name."},
            source="slack",
            expected_class=TriggerClass.NO_CHANGE,
            notes="Alert name already present — no update needed.",
        ),
        WikiUpdaterCase(
            id="pi-noc-02-stack-already-listed",
            surface="process_instruction",
            wiki_path="services/billing/overview.md",
            current_body=(
                "# Billing service\n\n"
                "Built on FastAPI + Postgres. Stripe webhooks land at `/webhooks/stripe`.\n"
                "Background workers run under pgmq.\n"
            ),
            payload={"instruction": ("We use Stripe for payments — add that to the billing page.")},
            source="manual",
            expected_class=TriggerClass.NO_CHANGE,
            notes="Stripe is already mentioned; nothing new to add.",
        ),
        WikiUpdaterCase(
            id="pi-noc-03-trivial-restatement",
            surface="process_instruction",
            wiki_path="teams/platform/charter.md",
            current_body=(
                "# Platform team charter\n\n"
                "We own the build, deploy, observability, and developer experience surfaces."
                " Our quarterly OKRs prioritize p95 deploy latency under 8 minutes.\n"
            ),
            payload={"instruction": "Platform team owns developer experience."},
            source="connector:notion",
            expected_class=TriggerClass.NO_CHANGE,
            notes="Restates ownership already in the charter.",
        ),
        WikiUpdaterCase(
            id="pi-noc-04-empty-payload",
            surface="process_instruction",
            wiki_path="services/search/index.md",
            current_body=(
                "# Search\n\n"
                "OpenSearch-backed BM25 over the wiki corpus. Reindex runs on every doc commit.\n"
            ),
            payload={"instruction": ""},
            source="webhook",
            expected_class=TriggerClass.NO_CHANGE,
            notes="Empty instruction — must not invent updates.",
        ),
        WikiUpdaterCase(
            id="pi-noc-05-already-fresh-after-recent-edit",
            surface="process_instruction",
            wiki_path="oncall/rotation.md",
            current_body=(
                "# Oncall rotation\n\n"
                "Weekly rotation Mon 09:00 PT. Handoff in #oncall-handoff with a brief"
                " of any open incidents and pending toil.\n\n"
                "## Current schedule\n\n"
                "See the PagerDuty schedule `eng-primary`.\n"
            ),
            payload={
                "instruction": "Rotation handoff happens Mondays — make sure that's documented."
            },
            source="slack",
            expected_class=TriggerClass.NO_CHANGE,
            notes="Handoff timing already present.",
        ),
    ]


def _process_change_cases() -> list[WikiUpdaterCase]:
    """Payload introduces genuinely new info — must return a new body."""
    return [
        WikiUpdaterCase(
            id="pi-chg-01-add-staging-endpoint",
            surface="process_instruction",
            wiki_path="services/auth/endpoints.md",
            current_body=(
                "# Auth endpoints\n\n"
                "## Production\n\n"
                "- `https://auth.example.com/login`\n"
                "- `https://auth.example.com/logout`\n"
            ),
            payload={
                "instruction": (
                    "We have a staging environment at https://auth.staging.example.com"
                    " — add a Staging section."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim(
                    "staging-host",
                    "the page documents the staging host https://auth.staging.example.com",
                ),
            ],
            expected_facts_preserved=[
                _claim("prod-login", "the production login endpoint is documented"),
                _claim("prod-logout", "the production logout endpoint is documented"),
            ],
            notes="Pure additive — staging section appears, prod stays.",
        ),
        WikiUpdaterCase(
            id="pi-chg-02-deprecation-notice",
            surface="process_instruction",
            wiki_path="api/v1/users.md",
            current_body=(
                "# Users API v1\n\n"
                "Status: stable.\n\n"
                "## Endpoints\n\n"
                "- `GET /v1/users` — list users\n"
                "- `POST /v1/users` — create user\n"
            ),
            payload={
                "instruction": ("v1 is deprecated as of 2026-04-01; clients should move to v2.")
            },
            source="connector:github",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("deprecated-date", "v1 is deprecated as of 2026-04-01"),
                _claim("migrate-v2", "clients should move to v2"),
            ],
            expected_facts_preserved=[
                _claim("list-endpoint", "GET /v1/users lists users"),
                _claim("create-endpoint", "POST /v1/users creates a user"),
            ],
            notes="Deprecation banner + endpoint list must persist.",
        ),
        WikiUpdaterCase(
            id="pi-chg-03-new-runbook-step",
            surface="process_instruction",
            wiki_path="ops/runbooks/db-failover.md",
            current_body=(
                "# DB failover runbook\n\n"
                "1. Confirm primary is unreachable (`pg_isready` from two regions).\n"
                "2. Promote the standby with `repmgr standby promote`.\n"
                "3. Update the connection string in HashiCorp Vault under `db/primary`.\n"
            ),
            payload={
                "instruction": (
                    "Before promoting, also drain the connection pool by setting"
                    " PGBOUNCER_PAUSE=1 in the pool config and reloading."
                )
            },
            source="slack",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("pgbouncer-pause", "PGBOUNCER_PAUSE=1 must be set before promotion"),
            ],
            expected_facts_preserved=[
                _claim("pg-isready", "the runbook still requires pg_isready from two regions"),
                _claim("repmgr", "the runbook still uses repmgr standby promote"),
                _claim("vault-update", "the runbook still updates Vault db/primary"),
            ],
            notes="Inserts a step at the right position; rest of the steps must persist.",
        ),
        WikiUpdaterCase(
            id="pi-chg-04-replace-tool",
            surface="process_instruction",
            wiki_path="services/cache/overview.md",
            current_body=(
                "# Cache service\n\n"
                "Memcached cluster fronts the recommendations API. Eviction is LRU."
                " Capacity 32 GB across 4 nodes.\n"
            ),
            payload={
                "instruction": (
                    "We migrated off Memcached to Redis last sprint. Same 32 GB across"
                    " 4 nodes, same LRU eviction."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("redis-now", "the cache service now runs on Redis"),
            ],
            expected_facts_preserved=[
                _claim("lru-eviction", "eviction is LRU"),
                _claim("capacity", "capacity is 32 GB across 4 nodes"),
            ],
            notes="Replacement with carry-over of operational facts.",
        ),
        WikiUpdaterCase(
            id="pi-chg-05-add-owner",
            surface="process_instruction",
            wiki_path="services/notifications/overview.md",
            current_body=(
                "# Notifications service\n\n"
                "Sends transactional email + push. Throughput 50/s steady, 500/s burst.\n"
            ),
            payload={
                "instruction": (
                    "Pia Lopez owns this service as of this week. Add an Owner section."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("owner-name", "Pia Lopez is named as the owner"),
            ],
            expected_facts_preserved=[
                _claim("throughput", "throughput numbers 50/s steady and 500/s burst remain"),
            ],
            notes="Personnel addition without removing operational facts.",
        ),
        WikiUpdaterCase(
            id="pi-chg-06-correct-typo-and-fact",
            surface="process_instruction",
            wiki_path="services/payments/sla.md",
            current_body=(
                "# Payments SLA\n\nAvilability target 99.9% monthly. Latency p95 < 200ms.\n"
            ),
            payload={
                "instruction": (
                    "Fix the typo 'Avilability' → 'Availability', and the target is"
                    " actually 99.95% per the contract."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("availability-fixed", "the spelling is 'Availability'"),
                _claim("target-995", "the availability target is 99.95% monthly"),
            ],
            expected_facts_preserved=[
                _claim("latency-target", "p95 latency target under 200ms remains"),
            ],
            notes="Two corrections, one of them factual. Watches for silent loss of latency line.",
        ),
        WikiUpdaterCase(
            id="pi-chg-07-add-link",
            surface="process_instruction",
            wiki_path="services/search/runbook.md",
            current_body=(
                "# Search runbook\n\n"
                "Restart the OpenSearch coordinator with `kubectl rollout restart"
                " statefulset/os-coord`.\n"
            ),
            payload={
                "instruction": (
                    "Link to the OpenSearch dashboard at https://grafana.example.com/d/os-coord"
                    " in the runbook."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("dashboard-url", "the dashboard URL grafana.example.com/d/os-coord appears"),
            ],
            expected_facts_preserved=[
                _claim("restart-cmd", "the kubectl rollout restart command remains"),
            ],
            notes="Simple link addition.",
        ),
        WikiUpdaterCase(
            id="pi-chg-08-cron-schedule",
            surface="process_instruction",
            wiki_path="jobs/nightly-reindex.md",
            current_body=(
                "# Nightly reindex\n\n"
                "Reindex job runs at 02:00 UTC on weekdays. Owner: search team.\n"
            ),
            payload={
                "instruction": (
                    "Schedule changed to 03:00 UTC daily (including weekends) starting this Monday."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("new-time", "the job runs at 03:00 UTC"),
                _claim("daily", "the job runs daily, not weekdays-only"),
            ],
            expected_facts_preserved=[
                _claim("owner", "the search team is still listed as owner"),
            ],
            notes="Time + cadence change. Owner must persist.",
        ),
    ]


def _process_bloat_bait() -> list[WikiUpdaterCase]:
    """Long payloads that add zero new info — must not blow up the page."""
    long_filler = (
        "Some additional context, written verbosely. " * 60
    )  # ~3 KB of noise that restates nothing.
    return [
        WikiUpdaterCase(
            id="pi-chg-bloat-01-verbose-restatement",
            surface="process_instruction",
            wiki_path="services/orders/api.md",
            current_body=(
                "# Orders API\n\nAuth required (Bearer). Rate limit 100 req/min per token.\n"
            ),
            payload={
                "instruction": (
                    "Long-winded restatement: %s The orders API requires auth and has a rate limit."
                    % long_filler
                )
            },
            source="connector:notion",
            expected_class=TriggerClass.NO_CHANGE,
            max_bloat_ratio=1.2,
            notes="Tests bloat resistance — payload is huge but info-free.",
            tags=["bloat-bait"],
        ),
        WikiUpdaterCase(
            id="pi-chg-bloat-02-real-change-but-tight",
            surface="process_instruction",
            wiki_path="services/orders/rate-limits.md",
            current_body=(
                "# Orders rate limits\n\n"
                "- Standard: 100 req/min per token\n"
                "- Enterprise: 1000 req/min per token\n"
            ),
            payload={
                "instruction": (
                    "Lots of context (most irrelevant): %s The bottom line is: standard tier is now "
                    "200 req/min per token." % long_filler
                )
            },
            source="connector:notion",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("standard-200", "standard tier rate limit is 200 req/min per token"),
            ],
            expected_facts_preserved=[
                _claim("enterprise-1000", "enterprise tier rate limit is 1000 req/min per token"),
            ],
            max_bloat_ratio=1.5,
            notes="Real one-line change buried in noise. Must not balloon the page.",
            tags=["bloat-bait"],
        ),
    ]


def _process_loss_bait() -> list[WikiUpdaterCase]:
    """Payloads that change one fact but tempt the model to drop others."""
    return [
        WikiUpdaterCase(
            id="pi-chg-loss-01-update-version",
            surface="process_instruction",
            wiki_path="services/python-runtime/notes.md",
            current_body=(
                "# Python runtime\n\n"
                "Production runs Python 3.12 on Linux. CPU-bound workers use uvloop."
                " Memory budget 2 GB per worker. Restart policy: rolling, 1 at a time.\n\n"
                "## Known issues\n\n"
                "- libpq segfault on shutdown when KEEPALIVE_INTERVAL < 5 (see bug-431)\n"
            ),
            payload={"instruction": "Upgrade to Python 3.13 next sprint."},
            source="slack",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("py313", "Python 3.13 is mentioned as the upcoming/current version"),
            ],
            expected_facts_preserved=[
                _claim("uvloop", "uvloop is still documented for CPU-bound workers"),
                _claim("mem-budget", "the 2 GB memory budget per worker is still documented"),
                _claim("restart-policy", "the rolling restart policy is still documented"),
                _claim("libpq-bug", "the libpq segfault known issue is still documented"),
            ],
            notes="One-line update tempts the model to drop the known-issues section.",
            tags=["loss-bait"],
        ),
        WikiUpdaterCase(
            id="pi-chg-loss-02-rebrand",
            surface="process_instruction",
            wiki_path="services/checkout/overview.md",
            current_body=(
                "# Checkout service\n\n"
                "Owns the order placement flow. Talks to: Inventory, Payments, Fraud-Detection."
                " On Fraud-Detection timeout (>2s), falls back to soft-allow with manual review.\n\n"
                "## SLOs\n\n"
                "- p95 latency under 350ms\n"
                "- availability 99.95% monthly\n"
            ),
            payload={
                "instruction": (
                    "Service is being renamed from 'Checkout' to 'OrderFlow' — update the name."
                )
            },
            source="manual",
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("renamed", "the service is now called OrderFlow"),
            ],
            expected_facts_preserved=[
                _claim(
                    "downstream-services",
                    "Inventory, Payments, and Fraud-Detection are still listed",
                ),
                _claim(
                    "fraud-fallback",
                    "the soft-allow + manual-review fallback for Fraud-Detection timeout is still documented",
                ),
                _claim("slo-latency", "p95 under 350ms is still documented"),
                _claim("slo-availability", "99.95% monthly availability is still documented"),
            ],
            notes="A rename tempts a from-scratch rewrite that loses SLOs and fallback details.",
            tags=["loss-bait"],
        ),
    ]


def _reconcile_no_change_cases() -> list[WikiUpdaterCase]:
    return [
        WikiUpdaterCase(
            id="rd-noc-01-already-up-to-date",
            surface="reconcile_document",
            wiki_path="services/billing/integration-notes.md",
            current_body=(
                "# Billing integration notes\n\n"
                "Stripe webhooks land at `/webhooks/stripe`. Signature verification uses"
                " `Stripe-Signature` header with the `whsec_*` secret stored in Vault.\n"
            ),
            doc_title="Stripe webhook integration",
            doc_url="https://stripe.com/docs/webhooks/signatures",
            doc_content=(
                "Stripe sends webhooks to your endpoint. Verify the Stripe-Signature header"
                " with your whsec_* secret."
            ),
            expected_class=TriggerClass.NO_CHANGE,
            notes="Wiki already documents the signature flow.",
        ),
        WikiUpdaterCase(
            id="rd-noc-02-stale-doc-newer-wiki",
            surface="reconcile_document",
            wiki_path="services/auth/jwt.md",
            current_body=(
                "# JWT issuance\n\n"
                "Tokens signed RS256. Key rotation every 30 days via `key-rotator` cron.\n"
            ),
            doc_title="Auth runbook (legacy)",
            doc_url="https://legacy.example.com/auth",
            doc_content=(
                "Auth tokens are signed with HS256. (This doc has not been updated since 2023.)"
            ),
            expected_class=TriggerClass.NO_CHANGE,
            notes="External doc is older — wiki should not regress.",
        ),
        WikiUpdaterCase(
            id="rd-noc-03-tangential-mention",
            surface="reconcile_document",
            wiki_path="services/search/architecture.md",
            current_body=(
                "# Search architecture\n\n"
                "OpenSearch 2.13 cluster, 3 data nodes, 2 coordinator nodes. BM25 over"
                " a custom tokenizer that lower-cases and strips diacritics.\n"
            ),
            doc_title="Q4 engineering update",
            doc_url="https://docs.example.com/q4",
            doc_content=(
                "Among other things this quarter: the search team continued running OpenSearch"
                " and shipped two reliability fixes."
            ),
            expected_class=TriggerClass.NO_CHANGE,
            notes="External doc mentions search tangentially with no new operational fact.",
        ),
    ]


def _reconcile_change_cases() -> list[WikiUpdaterCase]:
    return [
        WikiUpdaterCase(
            id="rd-chg-01-new-spec-pdf",
            surface="reconcile_document",
            wiki_path="services/notifications/templates.md",
            current_body=(
                "# Notification templates\n\n"
                "Render with Handlebars. Templates live in `notifications/templates/*.hbs`.\n"
            ),
            doc_title="Notification template authoring guide",
            doc_url="https://docs.example.com/templates",
            doc_content=(
                "Every template must include a plaintext fallback. The plaintext fallback"
                " goes in a sibling `.txt` file with the same basename."
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("plaintext-fallback", "each template requires a plaintext fallback"),
                _claim(
                    "txt-sibling",
                    "the plaintext goes in a sibling .txt file with the same basename",
                ),
            ],
            expected_facts_preserved=[
                _claim("handlebars", "Handlebars rendering is still documented"),
                _claim("templates-path", "templates live in notifications/templates/*.hbs"),
            ],
            notes="External spec adds a new rule — wiki must absorb it.",
        ),
        WikiUpdaterCase(
            id="rd-chg-02-incident-postmortem",
            surface="reconcile_document",
            wiki_path="ops/incidents/2026-04-12-cache-stampede.md",
            current_body=(
                "# 2026-04-12 cache stampede\n\n"
                "## Summary\n\n"
                "Recommendations API saw 5x normal origin load when a cache flush hit during peak.\n"
            ),
            doc_title="Postmortem: 2026-04-12 cache stampede",
            doc_url="https://postmortems.example.com/2026-04-12",
            doc_content=(
                "Root cause: a manual `redis-cli FLUSHDB` invoked during a Memcached → Redis"
                " migration. Action items: (1) require two-person approval for FLUSHDB,"
                " (2) add request-coalescing in front of the recs cache, owner: Wong (due 2026-05-01)."
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim(
                    "root-cause", "manual redis-cli FLUSHDB during migration is named as root cause"
                ),
                _claim(
                    "action-1",
                    "two-person approval requirement for FLUSHDB is listed as an action item",
                ),
                _claim(
                    "action-2",
                    "request-coalescing in front of the recs cache is listed as an action item",
                ),
                _claim("action-owner", "Wong is named as owner with due date 2026-05-01"),
            ],
            expected_facts_preserved=[
                _claim("summary", "the original summary line remains"),
            ],
            notes="Postmortem brings root cause + action items into the incident page.",
        ),
        WikiUpdaterCase(
            id="rd-chg-03-api-reference",
            surface="reconcile_document",
            wiki_path="api/v2/users.md",
            current_body=(
                "# Users API v2\n\n"
                "## Endpoints\n\n"
                "- `GET /v2/users` — list users\n"
                "- `POST /v2/users` — create user\n"
            ),
            doc_title="Users API v2 reference",
            doc_url="https://api-docs.example.com/v2/users",
            doc_content=(
                "v2 also supports DELETE /v2/users/{id} (soft-delete; restorable for 30 days)"
                " and PATCH /v2/users/{id} for partial updates."
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("delete-endpoint", "DELETE /v2/users/{id} is documented"),
                _claim("delete-soft", "soft-delete with 30-day restore window is documented"),
                _claim("patch-endpoint", "PATCH /v2/users/{id} is documented for partial updates"),
            ],
            expected_facts_preserved=[
                _claim("list-endpoint", "GET /v2/users is still documented"),
                _claim("create-endpoint", "POST /v2/users is still documented"),
            ],
            notes="Two endpoints added; existing ones must persist.",
        ),
        WikiUpdaterCase(
            id="rd-chg-04-policy-update",
            surface="reconcile_document",
            wiki_path="ops/data-retention.md",
            current_body=(
                "# Data retention\n\n"
                "Customer event logs retained for 90 days. Backups retained for 1 year.\n"
            ),
            doc_title="Updated retention policy",
            doc_url="https://legal.example.com/retention-2026",
            doc_content=(
                "Effective 2026-06-01, event log retention extends to 180 days. Backup retention"
                " unchanged."
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("new-retention", "event logs are retained for 180 days"),
                _claim("effective-date", "the change is effective 2026-06-01"),
            ],
            expected_facts_preserved=[
                _claim("backup-retention", "backups remain at 1 year"),
            ],
            notes="Compliance update; backup retention must persist.",
        ),
    ]


def _reconcile_irrelevant_cases() -> list[WikiUpdaterCase]:
    """External doc that doesn't apply to this wiki page — must IRRELEVANT."""
    return [
        WikiUpdaterCase(
            id="rd-irr-01-marketing-vs-runbook",
            surface="reconcile_document",
            wiki_path="ops/runbooks/db-failover.md",
            current_body=(
                "# DB failover runbook\n\n"
                "Promote standby with `repmgr standby promote`. Update Vault `db/primary`.\n"
            ),
            doc_title="2026 product roadmap announcement",
            doc_url="https://marketing.example.com/roadmap-2026",
            doc_content=(
                "We're excited to announce our 2026 roadmap, focused on AI-powered productivity"
                " for the modern team. Look out for new launches every quarter."
            ),
            expected_class=TriggerClass.IRRELEVANT,
            notes="Marketing announcement has nothing to do with the DB runbook.",
            tags=["irrelevant-bait"],
        ),
        WikiUpdaterCase(
            id="rd-irr-02-api-doc-vs-arch-decision",
            surface="reconcile_document",
            wiki_path="adr/0007-event-bus-choice.md",
            current_body=(
                "# ADR-0007: choosing an event bus\n\n"
                "## Decision\n\n"
                "We adopt Kafka over Kinesis for the central event bus. Trade-offs and"
                " constraints in the discussion below.\n"
            ),
            doc_title="Kafka REST proxy API reference",
            doc_url="https://docs.confluent.io/rest-proxy",
            doc_content=(
                "The Kafka REST proxy exposes HTTP endpoints to produce and consume from a"
                " Kafka cluster. GET /topics, POST /topics/{name}, etc."
            ),
            expected_class=TriggerClass.IRRELEVANT,
            notes="API reference is not an architecture decision update.",
            tags=["irrelevant-bait"],
        ),
        WikiUpdaterCase(
            id="rd-irr-03-doc-for-wrong-service",
            surface="reconcile_document",
            wiki_path="services/notifications/templates-overview.md",
            current_body=(
                "# Notification template overview\n\n"
                "Handlebars-rendered. Stored under `notifications/templates/*.hbs`.\n"
                "Reviewed quarterly by the messaging-platform team for compliance.\n"
            ),
            doc_title="Search reranker tuning guide",
            doc_url="https://docs.example.com/search/rerank",
            doc_content=(
                "The search reranker is a cross-encoder fine-tuned on click data. Tune the"
                " temperature parameter to balance precision and diversity."
            ),
            expected_class=TriggerClass.IRRELEVANT,
            notes="Doc is for search; page is for notifications.",
            tags=["irrelevant-bait"],
        ),
        WikiUpdaterCase(
            id="rd-irr-04-shared-domain-different-topic",
            surface="reconcile_document",
            wiki_path="ops/runbooks/auth-outage.md",
            current_body=(
                "# Auth outage runbook\n\n"
                "Page on-call. Check the auth-api dashboard. Fail over to standby if needed.\n"
            ),
            doc_title="Auth team onboarding guide",
            doc_url="https://hr.example.com/auth-onboarding",
            doc_content=(
                "Welcome to the auth team! Day 1: get your laptop, set up Okta, join"
                " #auth-team Slack. Day 2: shadow oncall."
            ),
            expected_class=TriggerClass.IRRELEVANT,
            notes="Both about auth, but onboarding is not a runbook update.",
            tags=["irrelevant-bait"],
        ),
    ]


def _reconcile_bloat_bait() -> list[WikiUpdaterCase]:
    long_doc = "Additional background and context. " * 80
    return [
        WikiUpdaterCase(
            id="rd-chg-bloat-01-verbose-doc",
            surface="reconcile_document",
            wiki_path="services/cache/policy.md",
            current_body=(
                "# Cache policy\n\n"
                "TTL 60s by default. Override per-key with the `cache-ttl-override` header.\n"
            ),
            doc_title="Cache TTL change request",
            doc_url="https://requests.example.com/cache-ttl-2026",
            doc_content=(
                "%s Bottom line: change the default TTL from 60s to 120s effective 2026-05-15."
                % long_doc
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("new-ttl", "the default TTL is 120s"),
                _claim("effective-date", "the change is effective 2026-05-15"),
            ],
            expected_facts_preserved=[
                _claim("override-header", "the cache-ttl-override header is still documented"),
            ],
            max_bloat_ratio=1.5,
            notes="One-line factual update wrapped in 4 KB of filler.",
            tags=["bloat-bait"],
        ),
    ]


def _reconcile_loss_bait() -> list[WikiUpdaterCase]:
    return [
        WikiUpdaterCase(
            id="rd-chg-loss-01-rewrite-temptation",
            surface="reconcile_document",
            wiki_path="services/billing/architecture.md",
            current_body=(
                "# Billing architecture\n\n"
                "## Components\n\n"
                "- `billing-api` — FastAPI app, owns invoice creation and Stripe webhook handling.\n"
                "- `billing-worker` — pgmq consumer, processes invoice events.\n"
                "- `billing-db` — Postgres 17 with the `pgmq` extension.\n\n"
                "## Constraints\n\n"
                "- Idempotency keys on every invoice mutation.\n"
                "- Webhook handler returns 200 within 5s or Stripe retries.\n"
            ),
            doc_title="Billing service: new BigQuery sink",
            doc_url="https://docs.example.com/billing-bigquery",
            doc_content=(
                "We added a BigQuery sink that streams invoice events from billing-worker"
                " for analytics. The sink is fire-and-forget; failures only emit a log line."
            ),
            expected_class=TriggerClass.CHANGE,
            expected_facts_present=[
                _claim("bq-sink", "the BigQuery sink streaming invoice events is documented"),
                _claim(
                    "fire-and-forget", "the sink is fire-and-forget with log-only failure handling"
                ),
            ],
            expected_facts_preserved=[
                _claim("billing-api", "billing-api is still documented"),
                _claim("billing-worker", "billing-worker is still documented"),
                _claim("billing-db", "billing-db is still documented"),
                _claim("idempotency", "the idempotency-key constraint is still documented"),
                _claim("webhook-5s", "the 5-second Stripe webhook constraint is still documented"),
            ],
            notes="External doc adds a component; existing architecture must persist.",
            tags=["loss-bait"],
        ),
    ]


def all_cases() -> list[WikiUpdaterCase]:
    return [
        *_process_no_change_cases(),
        *_process_change_cases(),
        *_process_bloat_bait(),
        *_process_loss_bait(),
        *_reconcile_no_change_cases(),
        *_reconcile_change_cases(),
        *_reconcile_irrelevant_cases(),
        *_reconcile_bloat_bait(),
        *_reconcile_loss_bait(),
    ]


def main() -> None:
    cases = all_cases()
    out_path = Path(__file__).resolve().parent / "cases.jsonl"
    seen_ids: set[str] = set()
    seen_bodies: set[str] = set()
    with out_path.open("w") as fh:
        for c in cases:
            if c.id in seen_ids:
                raise ValueError("duplicate case id: %s" % c.id)
            # Stub-LLM dry-run matches cases by current_body prefix; collisions
            # would route the wrong canned response. Wiki-path collisions are
            # fine (different content under the same path is realistic).
            body_key = c.current_body[:200]
            if body_key in seen_bodies:
                raise ValueError(
                    "duplicate current_body prefix in case %s — make the bodies distinct" % c.id
                )
            seen_ids.add(c.id)
            seen_bodies.add(body_key)
            fh.write(c.model_dump_json())
            fh.write("\n")
    print("wrote %d cases to %s" % (len(cases), out_path))


if __name__ == "__main__":
    main()
