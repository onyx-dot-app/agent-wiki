"""One-shot CLI to write the bundled onboarding pages into a running wiki.

Same write path as the fresh-install lifespan seed (commit + FTS
reindex + ACL stamping + MCP fan-out), but invocable against an
already-populated wiki so a developer can layer the onboarding pages
on top of an existing dev environment without nuking their work.

Usage (inside the backend container or from ``backend/`` locally):

    python -m app.scripts.seed_onboarding [--force]

Without ``--force``, files that already exist at the target path are
skipped. With ``--force``, they're overwritten. Either way, the script
is safe to re-run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.config import CONFIG
from app.utils.logging import setup_logging
from app.wiki.git import ensure_wiki_repo
from app.wiki.seed import write_seed_pages

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Write the bundled onboarding wiki pages into the running wiki."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite pages that already exist at the target paths.",
    )
    args = parser.parse_args(argv)

    ensure_wiki_repo()
    written = write_seed_pages(Path(CONFIG.wiki_dir), overwrite_existing=args.force)
    log.info("seed complete (%d page%s)", written, "" if written == 1 else "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
