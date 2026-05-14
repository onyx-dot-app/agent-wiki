"""Repo for ``page_working_dirs`` — per-(user, machine, page) working-dir binding.

The composite PK includes ``machine_id`` because the same user has
different local checkout paths on different laptops/desktops (
fix #5).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import PageWorkingDir
from app.db.session import session


def get_for_page(*, user_id: str, machine_id: str, wiki_path: str) -> str | None:
    with session() as s:
        row = s.scalar(
            select(PageWorkingDir).where(
                PageWorkingDir.user_id == user_id,
                PageWorkingDir.machine_id == machine_id,
                PageWorkingDir.wiki_path == wiki_path,
            )
        )
        return row.working_dir if row is not None else None


def set_for_page(
    *,
    user_id: str,
    machine_id: str,
    wiki_path: str,
    working_dir: str,
) -> None:
    stmt = pg_insert(PageWorkingDir).values(
        user_id=user_id,
        machine_id=machine_id,
        wiki_path=wiki_path,
        working_dir=working_dir,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "machine_id", "wiki_path"],
        set_={"working_dir": stmt.excluded.working_dir},
    )
    with session() as s:
        s.execute(stmt)


def clear(*, user_id: str, machine_id: str, wiki_path: str) -> None:
    with session() as s:
        s.execute(
            delete(PageWorkingDir).where(
                PageWorkingDir.user_id == user_id,
                PageWorkingDir.machine_id == machine_id,
                PageWorkingDir.wiki_path == wiki_path,
            )
        )
