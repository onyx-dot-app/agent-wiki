"""Reset the wiki to the bundled onboarding seed.

Intended for development and demo use: wipe the current wiki working
tree, clear the wiki-content tables in Postgres, clear the seed-once
marker, then re-run ``seed_if_empty`` so the workspace looks like a
fresh install. The seed marker is re-stamped by the seed call itself.

Preserved across the reset:

- Users, sessions, group membership, MCP tokens — you stay logged in.
- Admin settings (LLM provider, web tools, Braintrust, etc.).
- Document templates (starter templates for new pages).
- Chat sessions and messages.

Cleared:

- Every tracked file under ``WIKI_DIR`` (the working tree itself).
- ``documents`` — page metadata.
- ``acl_entries`` / ``wiki_owners`` — per-page permissions and owners.
- ``triggers`` and ``events`` — trigger cache and event log.
- ``agent_activity`` and ``document_drafts`` — in-flight session state.
- ``wiki_seed_state`` — the seed-once marker (re-stamped after seeding).

By default the existing wiki directory is archived to
``<wiki_dir>.bak-<timestamp>`` first; pass ``--no-archive`` to skip.

Usage:

    python -m app.scripts.reset_wiki_to_seed [--no-archive] [--yes]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.config import CONFIG
from app.db.session import session
from app.utils.logging import setup_logging
from app.wiki.git import ensure_wiki_repo
from app.wiki.seed import seed_if_empty

log = logging.getLogger(__name__)


# Tables wiped on reset. Order doesn't matter (no FKs between them).
_WIKI_CONTENT_TABLES = (
    "wiki_seed_state",
    "documents",
    "acl_entries",
    "wiki_owners",
    "triggers",
    "events",
    "agent_activity",
    "document_drafts",
)


def _archive_wiki_dir(target: Path) -> Path | None:
    """Copy ``target`` to a timestamped sibling. Returns the new path, or
    None if the source doesn't exist (nothing to archive)."""
    if not target.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = target.with_name(f"{target.name}.bak-{stamp}")
    shutil.copytree(target, bak, symlinks=True)
    log.info("archived %s -> %s", target, bak)
    return bak


def _clear_wiki_tables() -> None:
    with session() as s:
        for table in _WIKI_CONTENT_TABLES:
            s.execute(text(f"DELETE FROM {table}"))
            log.debug("cleared %s", table)
    log.info("cleared wiki-content tables (%d total)", len(_WIKI_CONTENT_TABLES))


def reset(*, archive: bool) -> None:
    target = Path(CONFIG.wiki_dir)

    if archive:
        _archive_wiki_dir(target)
    else:
        log.info("--no-archive: skipping backup of %s", target)

    if target.exists():
        shutil.rmtree(target)
        log.info("removed %s", target)

    _clear_wiki_tables()

    # Re-initialize repo and re-fire the seed-on-empty hook. The marker
    # was cleared above, so the seed will write the bundled pages and
    # re-stamp the marker.
    ensure_wiki_repo()
    seeded = seed_if_empty(CONFIG.wiki_dir)
    if not seeded:
        log.warning(
            "seed_if_empty returned False after reset — check that the bundled "
            "seed exists at the path resolved by app.wiki.seed.SEED_SOURCE_DIR"
        )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip backing up the current wiki dir before deleting it.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args(argv)

    if not args.yes:
        target = CONFIG.wiki_dir
        sys.stderr.write(
            f"This will WIPE the wiki at {target} and clear wiki-content tables "
            f"in {CONFIG.database_url.split('@')[-1]}. Continue? [y/N] "
        )
        sys.stderr.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer not in {"y", "yes"}:
            sys.stderr.write("aborted\n")
            return 1

    reset(archive=not args.no_archive)
    log.info("reset complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
