"""Creation preflight — the synchronous half of the on-create check.

Agent creates (chat/MCP ``write_doc``) run this alongside the commit: if the
new page collides case-insensitively with an existing path or is
byte-identical to an existing page, the create still succeeds and the tool
result carries the finding — the agent can fix it itself (retire its copy,
update the original) or tell the human, who may act on the on-page proposal
or simply ignore it. Surface, never block: the cleanup decision belongs to
the human (or, on auto-managed pages, to the auto-apply path), and an
ignorable suggestion must not stop the work.

The checks are the same instant-truth facts the on-create trigger detects
post-commit (case-collision, body-dup with template-echo precedence) —
this is prevention at the cheapest possible moment, and the async trigger
still covers everything the gate doesn't see (human/API creates, overridden
creates, non-create channels).

Contract, mirroring ``validate()``: **mechanical only, never the LLM** — one
git listing plus in-process hashing, so blocking a live tool call on it is
affordable. Callers fail open on any error: a broken preflight must never
block a write; the post-commit trigger and the banner are the safety net.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.wiki import git
from app.wiki.automanage.detectors.body_dup import MIN_BODY_BYTES
from app.wiki.automanage.detectors.template_echo import (
    blob_sha,
    template_body_blob_shas,
)


class CreationConflict(BaseModel):
    """One reason to pause a create, with the page it conflicts with."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["case_collision", "duplicate"]
    existing_path: str
    suggestion: str


def check_creation(path: str, body: str) -> list[CreationConflict]:
    """Instant-truth conflicts a create at ``path`` with ``body`` would cause.

    - ``case_collision`` — an existing path differs only by letter case
      (case-insensitive filesystems can materialize just one of them).
    - ``duplicate`` — an existing page is byte-identical, the body is
      substantial (body-dup's floor), and it isn't a template body (creating
      a template instance is legitimate; template-echo owns untouched
      skeletons on its own clock).
    """
    conflicts: list[CreationConflict] = []
    lower = path.lower()
    listing = git.list_paths_with_blob_sha()

    for existing, _ in listing:
        if existing != path and existing.lower() == lower:
            conflicts.append(
                CreationConflict(
                    kind="case_collision",
                    existing_path=existing,
                    suggestion=(
                        f"“{path}” differs only by letter case from "
                        f"“{existing}” — case-insensitive filesystems can hold "
                        "only one. Use the existing page or pick a distinct "
                        "name."
                    ),
                )
            )

    if len(body.encode()) >= MIN_BODY_BYTES:
        sha = blob_sha(body)
        if sha not in template_body_blob_shas():
            for existing, existing_sha in listing:
                if existing_sha == sha and existing.endswith(".md"):
                    conflicts.append(
                        CreationConflict(
                            kind="duplicate",
                            existing_path=existing,
                            suggestion=(
                                f"the body is byte-identical to “{existing}” — "
                                "update that page instead of creating a copy."
                            ),
                        )
                    )
    return conflicts
