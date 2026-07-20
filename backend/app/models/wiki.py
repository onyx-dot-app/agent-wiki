"""Internal domain types for the wiki layer."""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict


class ChangeKind(str, Enum):
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    SCHEDULE = "schedule"


class PageKind(str, Enum):
    """Whether a wiki path names a page (a ``.md`` file) or a folder.

    The single representation of this distinction across the wiki layer —
    ACLs (``resource_kind``), update policies, and the Trash view all classify
    paths this way. ``str``-valued so it compares/serializes as ``"page"`` /
    ``"folder"`` (matching the stored column values and the DB CHECK
    constraints) with no ``.value`` juggling at call sites.
    """

    PAGE = "page"
    FOLDER = "folder"

    @classmethod
    def of(cls, path: str) -> "PageKind":
        """Classify ``path`` by extension: a ``.md`` file is a page, anything
        else (including the root ``""``) is a folder."""
        return cls.PAGE if path.endswith(".md") else cls.FOLDER


class PathMove(BaseModel):
    """One tracked file relocated by a move/rename commit: ``old`` → ``new``.

    ``git.move_path`` emits one per tracked file (a directory rename yields
    one per nested file, all sharing the same prefix swap); the move handlers
    (``notify.after_path_move`` → ACL / update-policy / co-edit / trigger
    re-keying) consume them.
    """

    model_config = ConfigDict(frozen=True)

    old: str
    new: str


class CommitResult(NamedTuple):
    """Result of a successful wiki commit.

    ``sha`` is the new commit SHA. ``old_body`` and ``new_body`` are the
    document content before and after the write, used by callers to produce
    diffs and broken-link reports.
    """

    sha: str
    old_body: str
    new_body: str


class ActorKind(str, Enum):
    """Valid ``provenance_ledger.actor_kind`` values. Single source of truth,
    mirrored by the CHECK constraint in ``app/db/models.py`` (same pattern as
    ``UserKind``)."""

    HUMAN = "human"
    AGENT = "agent"
    INGESTION = "ingestion"
    SYSTEM = "system"


class WriteProvenance(BaseModel):
    """Source facts for an ingestion write, threaded through the commit gateway
    into the provenance ledger. Set only for ingestion writes.

    Field names mirror the ``provenance_ledger`` source columns so the ledger
    insert can spread them.
    """

    model_config = ConfigDict(frozen=True)

    source_document_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    source_title: str | None = None


class Attribution(BaseModel):
    """Structured provenance for one commit, resolved ledger-first with a git
    author-string parse as the fallback for any commit with no ledger row.

    ``person`` and ``agent`` describe a human or agent write, ``source_*`` an
    ingestion write.
    """

    model_config = ConfigDict(frozen=True)

    actor_kind: ActorKind
    person: str | None = None
    agent: str | None = None
    source_document_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    source_title: str | None = None


class SourceRef(WriteProvenance):
    """One ingested document that has contributed to a page (the Sources tab
    list). Compact by design: identity and links, no content ranges.
    """

    last_updated: str


class CommitMaxRetriesError(Exception):
    """Raised by ``commit_and_fan_out`` when HEAD keeps moving past the retry budget."""

    def __init__(self, retries: int, current_sha: str) -> None:
        self.retries = retries
        self.current_sha = current_sha
        super().__init__(f"max retries ({retries}) exceeded, current_sha={current_sha}")
