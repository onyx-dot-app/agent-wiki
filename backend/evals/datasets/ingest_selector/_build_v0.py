"""Build the v0 ingest_selector eval dataset.

Run once:

    cd backend && uv run python -m evals.datasets.ingest_selector._build_v0

Produces ``cases.jsonl`` next to this file.
"""

from __future__ import annotations

from pathlib import Path

from evals.schema import IngestSelectorCandidate, IngestSelectorCase


def _candidate(path: str, body: str) -> IngestSelectorCandidate:
    return IngestSelectorCandidate(path=path, body=body)


def all_cases() -> list[IngestSelectorCase]:
    return [
        IngestSelectorCase(
            id="sel-01-one-true-hit",
            doc_title="Stripe webhook signature verification",
            doc_content=(
                "Stripe POSTs webhooks to your endpoint with a `Stripe-Signature` header."
                " Verify it with your `whsec_*` secret before processing."
            ),
            candidates=[
                _candidate(
                    "services/billing/integration-notes.md",
                    "Stripe webhooks land at /webhooks/stripe. Signature verification uses"
                    " the Stripe-Signature header with the whsec_* secret stored in Vault.",
                ),
                _candidate(
                    "services/auth/jwt.md",
                    "JWT issuance: tokens signed RS256. Key rotation every 30 days.",
                ),
                _candidate(
                    "ops/runbooks/db-failover.md",
                    "Promote standby with repmgr standby promote. Update Vault db/primary.",
                ),
            ],
            expected_kept_paths=["services/billing/integration-notes.md"],
            notes="One billing page matches; two unrelated pages should be dropped.",
        ),
        IngestSelectorCase(
            id="sel-02-multi-hit",
            doc_title="Cache stampede postmortem",
            doc_content=(
                "Recommendations API saw 5x normal origin load when a cache flush hit during"
                " peak. Root cause: manual redis-cli FLUSHDB. Action items: two-person approval,"
                " request-coalescing in front of the recs cache."
            ),
            candidates=[
                _candidate(
                    "ops/incidents/2026-04-12-cache-stampede.md",
                    "2026-04-12 cache stampede: Recommendations API saw 5x normal origin load.",
                ),
                _candidate(
                    "services/cache/policy.md",
                    "TTL 60s by default. Override per-key with the cache-ttl-override header.",
                ),
                _candidate(
                    "services/recommendations/overview.md",
                    "Recommendations API fronted by a Redis cache. 100k req/s steady.",
                ),
                _candidate(
                    "teams/platform/charter.md",
                    "We own the build, deploy, observability, and developer experience surfaces.",
                ),
            ],
            expected_kept_paths=[
                "ops/incidents/2026-04-12-cache-stampede.md",
                "services/cache/policy.md",
                "services/recommendations/overview.md",
            ],
            notes="Three relevant pages; team charter should be dropped.",
        ),
        IngestSelectorCase(
            id="sel-03-no-hits",
            doc_title="Q4 marketing roadmap",
            doc_content=(
                "Marketing campaigns for Q4: holiday push, year-end recap, new vertical launch."
            ),
            candidates=[
                _candidate(
                    "services/search/architecture.md",
                    "OpenSearch 2.13 cluster, 3 data nodes, 2 coordinator nodes.",
                ),
                _candidate(
                    "ops/runbooks/auth-outage.md",
                    "Page on-call. Check the auth-api dashboard. Fail over if needed.",
                ),
            ],
            expected_kept_paths=[],
            notes="Pure noise — selector should drop everything.",
            tags=["all-drop"],
        ),
        IngestSelectorCase(
            id="sel-04-single-relevant-among-many",
            doc_title="DELETE /v2/users semantics",
            doc_content=(
                "v2 supports DELETE /v2/users/{id} for soft-delete; restorable within 30 days."
            ),
            candidates=[
                _candidate(
                    "api/v2/users.md",
                    "Users API v2: GET /v2/users list, POST /v2/users create.",
                ),
                _candidate(
                    "api/v2/orders.md",
                    "Orders API v2: GET /v2/orders, POST /v2/orders.",
                ),
                _candidate(
                    "api/v2/payments.md",
                    "Payments API v2: idempotency keys required on every mutation.",
                ),
                _candidate(
                    "ops/data-retention.md",
                    "Customer event logs retained for 90 days. Backups retained for 1 year.",
                ),
            ],
            expected_kept_paths=["api/v2/users.md"],
            notes="Subtle overlap — retention page is about retention generally, not user soft-delete.",
        ),
        IngestSelectorCase(
            id="sel-05-large-batch",
            doc_title="OpenSearch upgrade to 2.14",
            doc_content=(
                "We are upgrading the search cluster from OpenSearch 2.13 to 2.14 next sprint."
                " Rolling restart per node; expected window 2h."
            ),
            candidates=[
                _candidate(
                    "services/search/architecture.md",
                    "OpenSearch 2.13 cluster, 3 data nodes, 2 coordinator nodes.",
                ),
                _candidate(
                    "services/search/runbook.md",
                    "Restart the OpenSearch coordinator with kubectl rollout restart.",
                ),
                _candidate(
                    "services/search/index.md",
                    "OpenSearch-backed BM25 over the wiki corpus. Reindex on every commit.",
                ),
                _candidate(
                    "services/billing/overview.md",
                    "Billing on FastAPI + Postgres. Stripe webhooks at /webhooks/stripe.",
                ),
                _candidate(
                    "services/notifications/overview.md",
                    "Notifications service sends transactional email + push.",
                ),
                _candidate(
                    "ops/runbooks/auth-outage.md",
                    "Page on-call. Check the auth-api dashboard. Fail over if needed.",
                ),
            ],
            expected_kept_paths=[
                "services/search/architecture.md",
                "services/search/runbook.md",
                "services/search/index.md",
            ],
            notes="Three search pages relevant; billing/notifications/auth unrelated.",
        ),
    ]


def main() -> None:
    cases = all_cases()
    out_path = Path(__file__).resolve().parent / "cases.jsonl"
    seen_ids: set[str] = set()
    with out_path.open("w") as fh:
        for c in cases:
            if c.id in seen_ids:
                raise ValueError("duplicate case id: %s" % c.id)
            seen_ids.add(c.id)
            fh.write(c.model_dump_json())
            fh.write("\n")
    print("wrote %d cases to %s" % (len(cases), out_path))


if __name__ == "__main__":
    main()
