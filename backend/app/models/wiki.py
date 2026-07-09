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


class CommitMaxRetriesError(Exception):
    """Raised by ``commit_and_fan_out`` when HEAD keeps moving past the retry budget."""

    def __init__(self, retries: int, current_sha: str) -> None:
        self.retries = retries
        self.current_sha = current_sha
        super().__init__(f"max retries ({retries}) exceeded, current_sha={current_sha}")
