"""Bundle and write the onboarding wiki pages.

The backend image ships ``backend/wiki_seed/`` baked in (see the
Dockerfile). On first boot, if the wiki working tree tracks no
markdown pages, ``seed_if_empty`` writes the bundled tree in via the
normal commit-then-notify path — so search indexing, ACL stamping,
and MCP fan-out all fire as if a user had created each page through
the UI. The lifespan invokes this *after* ``ensure_wiki_repo`` so the
git repo is already initialized when we start committing.

The CLI in ``app/scripts/seed_onboarding.py`` shares the helper below
to write pages onto an already-populated wiki without nuking it.
"""
from __future__ import annotations

import logging
from pathlib import Path

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
    from app.wiki.git import commit_file
    from app.wiki.notify import after_doc_write

    processed = 0
    for rel, body in iter_seed_pages():
        target = wiki_root / rel
        already_exists = target.exists()
        if already_exists and not overwrite_existing:
            log.info("seed skip %s (already exists)", rel)
            continue
        change_kind = "edit" if already_exists else "create"
        verb = "update" if already_exists else "add"
        sha = commit_file(rel, body, f"seed onboarding: {verb} {rel}", author=SEED_AUTHOR)
        after_doc_write(rel, sha, change_kind, actor=SEED_AUTHOR)
        log.info("seed %s %s @ %s", change_kind, rel, sha[:8])
        processed += 1
    return processed


def seed_if_empty(target_dir: str) -> bool:
    """If the wiki tracks no markdown pages, populate it from the bundled seed.

    Returns True if pages were written. Must be called *after*
    ``ensure_wiki_repo`` so the git repo exists. Detects "fresh" by
    looking for any tracked ``.md`` file (not by checking ``.git`` —
    the lifespan has already initialized git by this point).
    """
    if not SEED_SOURCE_DIR.is_dir():
        log.debug("no bundled wiki seed at %s, skipping", SEED_SOURCE_DIR)
        return False
    from app.wiki.git import list_paths

    if any(p.endswith(".md") for p in list_paths()):
        log.debug("wiki already has tracked pages, skipping seed")
        return False
    log.info("seeding empty wiki at %s from %s", target_dir, SEED_SOURCE_DIR)
    written = write_seed_pages(Path(target_dir), overwrite_existing=False)
    return written > 0
