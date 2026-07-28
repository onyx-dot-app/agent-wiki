"""Bundle and write the onboarding wiki pages.

The backend image ships ``backend/wiki_seed/`` baked in (see the
Dockerfile). On first boot, if the wiki working tree tracks no
markdown pages, ``seed_if_empty`` writes the bundled tree in via the
normal commit-then-notify path — so search indexing, ACL stamping,
and MCP fan-out all fire as if a user had created each page through
the UI. The lifespan invokes this *after* ``ensure_wiki_repo`` so the
git repo is already initialized when we start committing.

Seed-once contract: the hook stamps a marker in ``wiki_seed_state``
the first time it runs on a given database, whether it wrote the
seed or observed pre-existing content. Once that marker is set, the
seed never runs again — so a user who deletes every onboarding page
and reboots gets an empty wiki, not a re-seed. The marker lives in
Postgres on purpose, so wiping the wiki working tree alone doesn't
re-arm seeding.

Seeding indexes the pages it writes; a first boot over pre-existing
content skips seeding, and the lifespan's ``backfill_unindexed_pages``
indexes that content instead.

The CLI in ``app/scripts/seed_onboarding.py`` shares the helper below
to write pages onto an already-populated wiki without nuking it. The
CLI bypasses the marker because it's explicitly user-driven.
"""
from __future__ import annotations

from app.db.session import session
from app.models.wiki import ChangeKind
from app.wiki.git import commit_file, list_paths
from app.wiki.notify import after_doc_write
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.tasks.reindex import reindex_all_inline

log = logging.getLogger(__name__)


# /app/wiki_seed in the image; backend/wiki_seed in dev. Both work because
# this module lives at <root>/app/wiki/seed.py and the seed dir is a sibling
# of <root>/app/.
SEED_SOURCE_DIR = Path(__file__).resolve().parents[2] / "wiki_seed"

SEED_AUTHOR = "agent-wiki <system@agent-wiki>"


def iter_seed_pages() -> list[tuple[str, str]]:
    """Yield ``(wiki-relative path, body)`` for every .md file in the bundled
    seed, sorted so parents land before nested children."""
    if not SEED_SOURCE_DIR.is_dir():
        return []
    pages = [
        (src.relative_to(SEED_SOURCE_DIR).as_posix(), src.read_text())
        for src in SEED_SOURCE_DIR.rglob("*.md")
    ]
    pages.sort(key=lambda row: row[0])
    return pages


def write_seed_pages(
    wiki_root: Path,
    *,
    overwrite_existing: bool,
) -> int:
    """Write each bundled page into ``wiki_root`` via the normal commit path.

    Uses ``commit_file`` + ``after_doc_write`` so FTS reindex, ACL
    seeding, and MCP fan-out all fire — identical to a UI save. Files
    that already exist at the target path are skipped unless
    ``overwrite_existing`` is True. Returns the number of pages
    processed (whether created, updated, or no-op committed).
    """
    # Local imports to avoid pulling DB/notify deps into modules that just
    # want SEED_SOURCE_DIR.

    processed = 0
    for rel, body in iter_seed_pages():
        target = wiki_root / rel
        already_exists = target.exists()
        if already_exists and not overwrite_existing:
            log.info("seed skip %s (already exists)", rel)
            continue
        change_kind = ChangeKind.EDIT if already_exists else ChangeKind.CREATE
        verb = "update" if already_exists else "add"
        sha = commit_file(rel, body, f"seed onboarding: {verb} {rel}", author=SEED_AUTHOR)
        after_doc_write(rel, sha, change_kind, actor=SEED_AUTHOR)
        log.info("seed %s %s @ %s", change_kind, rel, sha[:8])
        processed += 1
    return processed


def _read_seed_marker() -> str | None:
    """Return the ``seeded_at`` ISO timestamp, or None if not yet stamped."""

    with session() as s:
        row = s.execute(
            text("SELECT seeded_at FROM wiki_seed_state WHERE id = 1")
        ).first()
    return row[0] if row else None


def _stamp_seed_marker() -> None:
    """Stamp ``seeded_at`` to now (UTC, ISO). Upserts the singleton row."""

    now_iso = datetime.now(timezone.utc).isoformat()
    with session() as s:
        s.execute(
            text(
                "INSERT INTO wiki_seed_state (id, seeded_at) VALUES (1, :ts) "
                "ON CONFLICT (id) DO UPDATE SET seeded_at = EXCLUDED.seeded_at"
            ),
            {"ts": now_iso},
        )
    log.info("wiki_seed_state marker stamped at %s", now_iso)


def seed_if_empty(target_dir: str) -> bool:
    """If this DB has never been seeded, populate the wiki from the bundled seed.

    Honors a one-shot marker (``wiki_seed_state.seeded_at``) so the
    seed never runs twice on the same database. Must be called *after*
    ``ensure_wiki_repo`` so the git repo exists, and *after*
    ``init_db`` so the marker table is migrated.

    Returns True if pages were written, False otherwise (marker
    already set, wiki has pre-existing content, or seed source
    missing). In the "wiki has pre-existing content" case the marker
    is still stamped — so future content deletions won't trigger a
    re-seed.
    """
    if _read_seed_marker() is not None:
        log.debug("wiki_seed_state marker already set, skipping seed")
        return False
    if not SEED_SOURCE_DIR.is_dir():
        log.debug("no bundled wiki seed at %s, skipping", SEED_SOURCE_DIR)
        return False

    if any(p.endswith(".md") for p in list_paths()):
        # Pre-existing content (admin seeded another way, migrating from
        # an older install). Stamp the marker so a future delete-all
        # doesn't trigger re-seeding. Indexing this content is the
        # lifespan's job (backfill_unindexed_pages).
        log.info("wiki already has tracked pages, stamping marker without seeding")
        _stamp_seed_marker()
        return False
    log.info("seeding empty wiki at %s from %s", target_dir, SEED_SOURCE_DIR)
    written = write_seed_pages(Path(target_dir), overwrite_existing=False)
    if written > 0:
        _stamp_seed_marker()
        reindex_all_inline()
    return written > 0
