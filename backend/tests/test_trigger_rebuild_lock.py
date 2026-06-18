"""rebuild_from_filesystem() serialises on a Postgres advisory lock.

Concurrent rebuilds (boot lifespan vs. after_path_move, or overlapping backend
processes) used to race the delete-all + re-insert into a triggers_pkey
UniqueViolation and crash startup. The rebuild now takes a transaction-scoped
advisory lock first, so a second caller waits instead of colliding.

This proves it deterministically: hold that lock on a separate connection and
assert the rebuild blocks until it's released, rather than trying to force the
race.
"""
from __future__ import annotations

import threading

from sqlalchemy import text

from app.db.session import get_engine
from app.triggers.repo import _REBUILD_ADVISORY_LOCK, rebuild_from_filesystem


def test_rebuild_blocks_while_advisory_lock_is_held(tmp_repo: object) -> None:
    engine = get_engine()
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            rebuild_from_filesystem()
        except BaseException as e:  # noqa: BLE001 - surface to the test thread
            errors.append(e)
        finally:
            done.set()

    # Hold the rebuild lock (session-scoped) on a dedicated connection. Session
    # and xact advisory locks share one key space, so the rebuild's
    # pg_advisory_xact_lock on the same key must wait for us to release it. The
    # `with` guarantees the connection (and thus the lock) is cleaned up.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _REBUILD_ADVISORY_LOCK})
        worker = threading.Thread(target=run)
        worker.start()
        try:
            # Blocked on the held lock — must not complete yet.
            assert not done.wait(timeout=1.5), "rebuild did not wait on the advisory lock"

            # Release the lock; the rebuild should now proceed and finish.
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _REBUILD_ADVISORY_LOCK})
            assert done.wait(timeout=10.0), "rebuild did not complete after lock release"
            assert not errors, f"rebuild raised: {errors}"
        finally:
            worker.join(timeout=5)
