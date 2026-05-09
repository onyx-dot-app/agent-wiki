"""BM25 ranked search over a synthetic 15-doc corpus.

Seeds 15 fake docs into the wiki repo, runs each through the real
``wiki_bm25_queue`` reindex task (synchronously thanks to
``immediate_queues``), and asserts that ``fts.search`` ranks the
designed "obvious winner" doc first for each test query. The corpus
is engineered so each query has exactly one strongly-matching doc and
distractors share at most one query term.
"""
from __future__ import annotations

import pytest

from app.db import fts
from app.tasks.reindex import reindex_path
from app.wiki import acl, git
from tests._seed import list_fts_rows


# ----- corpus -------------------------------------------------------------- #
# (path, body) — first 5 are designed winners, last 10 are distractors.

_CORPUS: list[tuple[str, str]] = [
    # 1. winner: "redis eviction allkeys-lru"
    (
        "infra/redis-eviction.md",
        "# Redis Eviction\n\n"
        "Redis eviction kicks in when maxmemory is reached. The allkeys-lru "
        "policy evicts the least recently used keys across the whole keyspace. "
        "Pick allkeys-lru when every key is a cache entry; pick volatile-lru "
        "when only TTL-bearing keys should be evicted. Tune maxmemory-samples "
        "to trade eviction accuracy for CPU.\n",
    ),
    # 2. winner: "postgres autovacuum bloat"
    (
        "db/postgres-vacuum.md",
        "# Postgres Autovacuum\n\n"
        "Autovacuum reclaims dead tuples to prevent table bloat in postgres. "
        "When autovacuum falls behind on a high-churn table, bloat grows and "
        "queries slow down. Lower autovacuum_vacuum_scale_factor on hot tables "
        "so autovacuum runs more often and bloat stays bounded.\n",
    ),
    # 3. winner: "kafka consumer rebalance protocol"
    (
        "streams/kafka-rebalance.md",
        "# Kafka Consumer Rebalance\n\n"
        "Kafka consumer rebalance reassigns partitions across the group when "
        "membership changes. The cooperative rebalance protocol replaces the "
        "stop-the-world eager rebalance protocol with incremental partition "
        "moves. Prefer the cooperative rebalance protocol for long-running "
        "kafka consumer fleets.\n",
    ),
    # 4. winner: "nginx gzip compression"
    (
        "web/nginx-gzip.md",
        "# Nginx Gzip\n\n"
        "Enable nginx gzip compression to shrink text responses on the wire. "
        "Set gzip on, gzip_types to the MIME types worth compressing, and "
        "gzip_min_length to skip tiny payloads. Nginx gzip compression trades "
        "CPU for bandwidth; benchmark before turning gzip_comp_level past 6.\n",
    ),
    # 5. winner: "terraform state lock dynamodb"
    (
        "iac/terraform-state.md",
        "# Terraform State Lock\n\n"
        "The terraform state backend uses a dynamodb table to hold the state "
        "lock so concurrent applies don't corrupt state. Configure the s3 "
        "backend with dynamodb_table set to a table that has LockID as the "
        "hash key. Without the dynamodb state lock, two terraform apply runs "
        "can race.\n",
    ),

    # ----- distractors: neutral infra topics, no overlap with winner phrases.
    (
        "infra/docker-networks.md",
        "# Docker Networks\n\n"
        "A docker bridge network connects containers on the same host. User "
        "defined bridges give containers DNS names that resolve to peers in "
        "the same bridge — for example a redis container reachable as "
        "redis:6379 from sibling services.\n",
    ),
    (
        "infra/k8s-pods.md",
        "# Kubernetes Pods\n\n"
        "A pod groups one or more containers that share a network namespace. "
        "Init containers run to completion before the main containers start.\n",
    ),
    (
        "db/mysql-replication.md",
        "# MySQL Replication\n\n"
        "Asynchronous replication streams binlog events from a primary to "
        "replicas. Semi-sync replication waits for one replica acknowledgement "
        "before the primary commits. The mechanism differs from postgres "
        "logical replication, which streams decoded changes per publication.\n",
    ),
    (
        "db/mongo-sharding.md",
        "# Mongo Sharding\n\n"
        "Sharded clusters split a collection across shards by shard key. "
        "Choose a shard key with high cardinality so chunks distribute "
        "evenly.\n",
    ),
    (
        "web/caddy-tls.md",
        "# Caddy TLS\n\n"
        "Caddy provisions TLS certificates from Let's Encrypt automatically "
        "for any host listed in the Caddyfile. Renewal happens in the "
        "background without operator intervention, which is the main reason "
        "operators pick caddy over nginx for small fleets.\n",
    ),
    (
        "web/haproxy-routing.md",
        "# HAProxy Routing\n\n"
        "HAProxy routes traffic across backends using ACLs on path, host, or "
        "header. Sticky sessions can be pinned with cookie based persistence "
        "or stick tables.\n",
    ),
    (
        "streams/rabbitmq-queues.md",
        "# RabbitMQ Queues\n\n"
        "Quorum queues replicate messages across nodes using Raft. Classic "
        "mirrored queues are deprecated in favor of quorum queues for "
        "durability. Teams migrating from kafka often pick quorum queues for "
        "the closest at-least-once semantics.\n",
    ),
    (
        "iac/ansible-playbooks.md",
        "# Ansible Playbooks\n\n"
        "Playbooks describe ordered plays of tasks against inventory hosts. "
        "Roles package reusable task bundles with their own variables and "
        "templates. Unlike terraform, ansible runs imperatively and does not "
        "track resource state in a backend.\n",
    ),
    (
        "monitoring/prometheus-targets.md",
        "# Prometheus Targets\n\n"
        "Service discovery feeds prometheus a list of scrape targets. The "
        "kubernetes_sd_config picks up pods and services with the right "
        "annotations.\n",
    ),
    (
        "monitoring/grafana-dashboards.md",
        "# Grafana Dashboards\n\n"
        "Dashboards group panels that query a configured data source. Panel "
        "variables let one dashboard render for any environment by selecting "
        "a label value.\n",
    ),
]

# (query, expected top-hit path) — one obvious-winner query per designed doc.
_QUERIES: list[tuple[str, str]] = [
    ("redis eviction allkeys-lru", "infra/redis-eviction.md"),
    ("postgres autovacuum bloat", "db/postgres-vacuum.md"),
    ("kafka consumer rebalance protocol", "streams/kafka-rebalance.md"),
    ("nginx gzip compression", "web/nginx-gzip.md"),
    ("terraform state lock dynamodb", "iac/terraform-state.md"),
]


@pytest.fixture
def seeded_corpus(tmp_repo, immediate_queues):
    """Commit all 15 docs and run them through the real reindex task.

    Uses ``immediate_queues`` so ``reindex_path`` executes inline on the
    ``wiki_bm25_queue`` queue — same code path as production, just
    synchronous. After this fixture returns, ``documents_fts`` has 15
    rows and the BM25 index is queryable.
    """
    for path, body in _CORPUS:
        git.commit_file(path, body, message=f"seed {path}")
        # Mirror production's ``after_doc_write`` create path so the
        # BM25 search visibility filter doesn't drop these pages —
        # bypassing the API skips the lifecycle hook otherwise.
        acl.on_page_created(path, owner_user_id=None)
        reindex_path(path)
    return _CORPUS


def test_corpus_fully_indexed(seeded_corpus):
    rows = list_fts_rows()
    assert len(rows) == len(_CORPUS)
    indexed_paths = {r["path"] for r in rows}
    assert indexed_paths == {p for p, _ in _CORPUS}


@pytest.mark.parametrize("query,expected_path", _QUERIES)
def test_top_hit_is_designed_winner(seeded_corpus, query, expected_path):
    hits = fts.search(query, limit=5)
    assert hits, f"no hits for {query!r}"
    assert hits[0].path == expected_path, (
        f"query={query!r} expected top={expected_path!r} "
        f"got={[h.path for h in hits]}"
    )


@pytest.mark.parametrize("query,expected_path", _QUERIES)
def test_top_hit_outscores_runner_up(seeded_corpus, query, expected_path):
    hits = fts.search(query, limit=5)
    if len(hits) < 2:
        pytest.skip("only one hit — score-margin assertion is vacuous")
    assert hits[0].score > hits[1].score, (
        f"query={query!r} top score not strictly greater than runner-up: "
        f"{[(h.path, h.score) for h in hits]}"
    )


def test_search_returns_no_hits_for_unrelated_query(seeded_corpus):
    assert fts.search("zzznonexistentterm", limit=5) == []


def test_snippet_bolds_query_terms(seeded_corpus):
    hits = fts.search("nginx gzip compression", limit=1)
    assert hits, "expected at least one hit"
    snippet = hits[0].snippet.lower()
    assert "**nginx**" in snippet or "**gzip**" in snippet, hits[0].snippet


def test_quoted_phrase_is_not_real_phrase_search(seeded_corpus):
    """pg_textsearch's ``to_bm25query`` does NOT honor quote syntax as
    Tantivy-style phrase queries — quotes are effectively stripped and
    the inner tokens are matched individually.

    'cooperative rebalance protocol' appears verbatim only in
    streams/kafka-rebalance.md. With real phrase semantics, the reversed
    wording 'protocol rebalance cooperative' should miss every doc. We
    observe instead that both queries return the same hit with the same
    score — proving quotes are ignored. This test pins that behaviour so
    future changes (e.g. swapping the parser or pre-processing the query)
    surface as a deliberate diff.
    """
    in_order = fts.search('"cooperative rebalance protocol"', limit=5)
    reversed_phrase = fts.search('"protocol rebalance cooperative"', limit=5)

    assert in_order, "quoted phrase query returned no hits"
    assert in_order[0].path == "streams/kafka-rebalance.md"
    assert reversed_phrase, "reversed quoted phrase unexpectedly returned no hits"
    assert reversed_phrase[0].path == "streams/kafka-rebalance.md"
    # Same hit, same score — quotes had no effect on ranking.
    assert reversed_phrase[0].score == in_order[0].score, (
        f"scores differ: in_order={in_order[0].score} "
        f"reversed={reversed_phrase[0].score}"
    )
