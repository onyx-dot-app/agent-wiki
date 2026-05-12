"""One-shot CLI to insert the bundled starter document templates.

Same path as the fresh-install lifespan seed
(``app.wiki.templates.seed_starter_templates_if_empty``) but invocable
against an already-populated install: skips any template whose
``name`` already exists, so the script is safe to re-run after pulling
new starter templates from the repo.

Usage (inside the backend container or from ``backend/`` locally):

    python -m app.scripts.seed_starter_templates
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.utils.logging import setup_logging
from app.wiki.templates import write_starter_templates

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Insert any missing bundled starter document templates."
    )
    parser.parse_args(argv)

    inserted = write_starter_templates(skip_existing=True)
    log.info(
        "starter templates seed complete (%d new row%s)",
        inserted,
        "" if inserted == 1 else "s",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
