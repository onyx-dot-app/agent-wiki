"""Internal domain types for the wiki layer."""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class ChangeKind(str, Enum):
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    SCHEDULE = "schedule"


class CommitResult(NamedTuple):
    """Result of a successful wiki commit.

    ``sha`` is the new commit SHA. ``old_body`` and ``new_body`` are the
    document content before and after the write, used by callers to produce
    diffs and broken-link reports.
    """

    sha: str
    old_body: str
    new_body: str


class AiRebaseMaxRetriesException(Exception):
    """Raised by ``commit_with_ai_rebase`` when HEAD keeps moving."""

    def __init__(self, retries: int, current_sha: str) -> None:
        self.retries = retries
        self.current_sha = current_sha
        super().__init__(f"max retries ({retries}) exceeded, current_sha={current_sha}")
