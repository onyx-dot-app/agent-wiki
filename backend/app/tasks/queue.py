"""Task queue backed by pgmq.

The decorator-shaped API the rest of the app uses:

  * ``@queue.task()``                              — register a handler
  * ``@queue.periodic_task(crontab(minute="*/5"))`` — register a cron handler
  * ``task(*args, **kwargs)``                      — direct call → enqueue
  * ``task.schedule(args=(...), eta=dt)``          — enqueue with a delay/eta
  * ``queue.immediate = True``                     — synchronous mode for tests
  * ``QueueFullError``                             — raised when at the cap

Each named ``TaskQueue`` persists its messages in the pgmq queue of the
same name (``pgmq.q_<name>``). pgmq's SQL functions (``pgmq.send``,
``pgmq.read``, ``pgmq.delete``, ``pgmq.archive``, ``pgmq.set_vt``) don't
have ORM equivalents, so they go through ``session.execute(text(...))``.

Worker semantics (see ``run_consumer``): ``concurrency`` worker threads
each long-poll the queue with ``qty=1``. Handlers run in-thread, so a
slow LLM task doesn't block its peers. On exception we push the
message's visibility timeout out exponentially via ``pgmq.set_vt`` and
let pgmq redeliver. Messages whose ``read_ct`` exceeds ``MAX_RETRIES``
get archived.

Periodic tasks are run by an in-process scheduler thread that holds a
per-queue advisory lock — only one process across the deployment fires
crons for a given queue, so scaling worker replicas no longer
double-fires schedules. ``cron_state`` (a small table) records each
task's last-fired timestamp so a restart picks up where the previous
process left off instead of silently skipping fires that came due
during downtime.

Shutdown: SIGTERM / SIGINT sets a stop event; worker threads finish
their current task and exit, the scheduler releases its lock by
disconnecting. Messages that are mid-flight at SIGKILL hold their VT
until it expires — that's unavoidable with pgmq.
"""
from __future__ import annotations

import json
import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Generator

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db.session import get_engine, session

log = logging.getLogger(__name__)


_DEFAULT_VT_SECONDS = 300       # how long a worker holds a message before it returns
_POLL_IDLE_SLEEP = 1.0          # seconds to wait when pgmq.read returns empty
_MAX_RETRIES = 3                # archive after this many redeliveries
_RETRY_BASE_SECONDS = 30        # first backoff; doubles per redelivery
_RETRY_MAX_SECONDS = 600        # cap so a long-failing task doesn't wait hours
_LEADER_RETRY_SECONDS = 30      # how long a non-leader scheduler sleeps before retrying

# Cap-check + send must be atomic against concurrent producers on the same
# queue, otherwise N producers each see size<limit and all insert. We hold a
# per-queue ``pg_advisory_xact_lock`` across the count + send. The lock auto-
# releases on COMMIT, so contention is bounded by the time of one INSERT.
_CAP_LOCK_PREFIX = "queue-cap:"

# Scheduler leadership lock. Only the holder runs the periodic scheduler for
# its queue; other replicas idle in the leader-retry loop. Held on a
# dedicated connection so the lock auto-releases on process death.
_SCHEDULER_LOCK_PREFIX = "queue-cron:"


class QueueFullError(RuntimeError):
    """Raised when a producer tries to enqueue past the configured cap."""

    def __init__(self, queue_name: str, size: int, limit: int) -> None:
        super().__init__(
            f"queue '{queue_name}' is full: {size} pending tasks at the configured "
            f"limit of {limit} (MAX_QUEUE_SIZE). Try again after the worker drains."
        )
        self.queue_name = queue_name
        self.size = size
        self.limit = limit


def crontab(
    *,
    minute: str = "*",
    hour: str = "*",
    day: str = "*",
    month: str = "*",
    day_of_week: str = "*",
) -> str:
    """Build a 5-field cron string from named fields. Parsed by ``croniter``."""
    return f"{minute} {hour} {day} {month} {day_of_week}"


# --------------------------------------------------------------------------- #
# Stop event — set by SIGTERM / SIGINT, observed by every worker thread plus  #
# the scheduler. Module-global so test fixtures don't have to thread it.      #
# --------------------------------------------------------------------------- #


_stop_event = threading.Event()


def _install_signal_handlers() -> None:
    """Wire SIGTERM / SIGINT → set the stop event. Idempotent.

    Only the main thread can install signal handlers (Python rule). We
    silently no-op when called from a worker thread so unit tests that
    spin up consumers from a non-main thread don't crash.
    """
    if threading.current_thread() is not threading.main_thread():
        return

    def _on_signal(signo: int, _frame: Any) -> None:
        log.info("queue: signal %d received — initiating graceful shutdown", signo)
        _stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # Re-installing signals during pytest can fail; safe to skip.
            pass


# --------------------------------------------------------------------------- #
# Task queue + handler                                                        #
# --------------------------------------------------------------------------- #


class _PeriodicEntry(BaseModel):
    cron_spec: str
    task_name: str


class TaskQueue(BaseModel):
    """One named queue + handler registry. Backed by pgmq.q_<name>."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    max_size: int
    immediate: bool = False
    handlers: dict[str, Callable[..., Any]] = Field(default_factory=dict)
    periodic: list[_PeriodicEntry] = Field(default_factory=list)

    def task(self) -> Callable[[Callable[..., Any]], "Task"]:
        def decorator(fn: Callable[..., Any]) -> "Task":
            name = fn.__name__
            if name in self.handlers:
                raise ValueError(f"task {name!r} already registered on queue {self.name!r}")
            self.handlers[name] = fn
            return Task(queue=self, name=name, fn=fn)
        return decorator

    def periodic_task(self, cron_spec: str) -> Callable[[Callable[..., Any]], "Task"]:
        def decorator(fn: Callable[..., Any]) -> "Task":
            wrapped = self.task()(fn)
            self.periodic.append(_PeriodicEntry(cron_spec=cron_spec, task_name=fn.__name__))
            return wrapped
        return decorator

    @contextmanager
    def immediate_mode(self) -> Generator["TaskQueue", None, None]:
        """Run handlers synchronously inside the ``with`` block; restore on exit.

        Tests are the only intended caller — flipping the flag globally
        used to leak across cases. The context manager saves the prior
        value and restores it even if the body raises, so leaks are
        impossible by construction. App code should never call this.

        Multiple queues can be entered with ``ExitStack``::

            with ExitStack() as stack:
                stack.enter_context(documents_queue.immediate_mode())
                stack.enter_context(triggers_queue.immediate_mode())
                ...
        """
        prev = self.immediate
        self.immediate = True
        try:
            yield self
        finally:
            self.immediate = prev

    # ----- size + enqueue ---------------------------------------------------

    def size(self) -> int:
        """Pending + future-scheduled message count, excluding in-flight.

        "In-flight" here means a worker has already read the message and
        its VT hasn't expired yet (``read_ct > 0 AND vt > now()``). Those
        represent work in progress and shouldn't count toward the cap or
        the healthcheck backlog — otherwise a slow consumer locks out
        producers entirely.

        Returns 0 in immediate mode (messages execute synchronously and
        never sit in pgmq).
        """
        if self.immediate:
            return 0
        with session() as s:
            row = s.execute(
                text(
                    f'SELECT count(*) AS n FROM pgmq."q_{self.name}" '
                    "WHERE read_ct = 0 OR vt <= now()"
                )
            ).mappings().first()
            if row is None:
                return 0
            n = row["n"]
            return int(n) if n is not None else 0

    def enqueue(
        self,
        task_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        eta: datetime | None = None,
        delay: int | None = None,
    ) -> None:
        if self.immediate:
            handler = self.handlers[task_name]
            handler(*args, **(kwargs or {}))
            return

        delay_seconds = _resolve_delay(eta, delay)
        body = {"task": task_name, "args": list(args), "kwargs": dict(kwargs or {})}

        # Single transaction: take the per-queue advisory lock, count
        # pending + future-scheduled rows, enqueue if under the cap. The
        # lock serializes only enqueues *on this queue*, so the three
        # queues stay independent. Released on commit/rollback.
        with session() as s:
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": _CAP_LOCK_PREFIX + self.name},
            )
            size = s.execute(
                text(
                    f'SELECT count(*) FROM pgmq."q_{self.name}" '
                    "WHERE read_ct = 0 OR vt <= now()"
                )
            ).scalar() or 0
            if size >= self.max_size:
                raise QueueFullError(self.name, size, self.max_size)
            s.execute(
                text("SELECT pgmq.send(:q, CAST(:msg AS jsonb), :delay)"),
                {"q": self.name, "msg": json.dumps(body), "delay": delay_seconds},
            )


class Task(BaseModel):
    """Wrapper around a registered handler. Calling it enqueues."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    queue: TaskQueue
    name: str
    fn: Callable[..., Any]

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Direct call enqueues — never executes synchronously unless
        ``queue.immediate`` is set (only in tests)."""
        self.queue.enqueue(self.name, args, kwargs)

    def schedule(
        self,
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        eta: datetime | None = None,
        delay: int | None = None,
    ) -> None:
        """Enqueue with a future ``eta`` or relative ``delay`` (seconds)."""
        self.queue.enqueue(self.name, tuple(args), kwargs or {}, eta=eta, delay=delay)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _resolve_delay(eta: datetime | None, delay: int | None) -> int:
    """Translate an ``eta`` / ``delay`` pair into pgmq's seconds-from-now."""
    if eta is None and delay is None:
        return 0
    if eta is not None and delay is not None:
        raise ValueError("pass either eta or delay, not both")
    if delay is not None:
        return max(0, int(delay))
    assert eta is not None
    if eta.tzinfo is None:
        eta = eta.replace(tzinfo=timezone.utc)
    seconds = (eta - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(seconds))


def _retry_backoff_seconds(read_ct: int) -> int:
    """Exponential backoff for redelivery: 30s, 60s, 120s, … capped.

    ``read_ct`` is the number of times the message has been delivered
    *including* the failed run we just observed (``read_ct=1`` on first
    failure).
    """
    delay = _RETRY_BASE_SECONDS * (2 ** max(0, read_ct - 1))
    return min(_RETRY_MAX_SECONDS, delay)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Consumer                                                                    #
# --------------------------------------------------------------------------- #


def run_consumer(
    queue: TaskQueue,
    *,
    concurrency: int = 1,
    vt_seconds: int = _DEFAULT_VT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> None:
    """Run ``concurrency`` worker threads against ``queue``.

    Each thread independently long-polls pgmq with ``qty=1`` and runs
    the handler in-thread, so handlers run truly in parallel up to
    ``concurrency``. The scheduler (if any periodic tasks are
    registered) runs in its own daemon thread guarded by an advisory
    lock.

    Returns when SIGTERM / SIGINT is delivered and all worker threads
    have drained. Workers don't pick up new messages after the stop
    event fires; in-flight handlers run to completion.
    """
    _install_signal_handlers()
    log.info(
        "queue %s: consumer up (concurrency=%d, vt=%ds)",
        queue.name, concurrency, vt_seconds,
    )

    if queue.periodic:
        threading.Thread(
            target=_run_periodic_scheduler,
            args=(queue,),
            name=f"scheduler-{queue.name}",
            daemon=True,
        ).start()

    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix=f"worker-{queue.name}"
    ) as pool:
        for i in range(concurrency):
            pool.submit(_worker_loop, queue, i, vt_seconds, max_retries)
        # ThreadPoolExecutor's __exit__ joins all submitted callables.
        # Worker loops exit when ``_stop_event`` is set.

    log.info("queue %s: consumer drained, exiting", queue.name)


def _worker_loop(
    queue: TaskQueue, worker_id: int, vt_seconds: int, max_retries: int
) -> None:
    log.debug("queue %s: worker %d ready", queue.name, worker_id)
    while not _stop_event.is_set():
        try:
            msgs = _pgmq_read(queue.name, vt=vt_seconds, qty=1)
        except Exception:
            log.exception("queue %s: pgmq.read failed", queue.name)
            _stop_event.wait(_POLL_IDLE_SLEEP)
            continue
        if not msgs:
            _stop_event.wait(_POLL_IDLE_SLEEP)
            continue
        # Even if stop fired between read and process, finish what we read —
        # otherwise the message holds its VT for the full vt_seconds before
        # redelivery. Running it now is the polite thing to do.
        for msg in msgs:
            _process_one(queue, msg, max_retries=max_retries)
    log.debug("queue %s: worker %d stopped", queue.name, worker_id)


def _process_one(queue: TaskQueue, msg: dict[str, Any], *, max_retries: int) -> None:
    msg_id: int = msg["msg_id"]
    read_ct: int = msg["read_ct"]
    body: dict[str, Any] = msg["message"]

    task_name: str = body.get("task", "")
    args: list[Any] = body.get("args") or []
    kwargs: dict[str, Any] = body.get("kwargs") or {}

    handler = queue.handlers.get(task_name)
    if handler is None:
        log.error(
            "queue %s: no handler for task %r — archiving msg %s",
            queue.name, task_name, msg_id,
        )
        _pgmq_archive(queue.name, msg_id)
        return

    try:
        log.debug("queue %s: run %s msg=%s read_ct=%d", queue.name, task_name, msg_id, read_ct)
        handler(*args, **kwargs)
    except Exception:
        log.exception(
            "queue %s: task %s failed (msg=%s read_ct=%d)",
            queue.name, task_name, msg_id, read_ct,
        )
        if read_ct >= max_retries:
            log.warning(
                "queue %s: task %s exceeded retries (read_ct=%d) — archiving msg %s",
                queue.name, task_name, read_ct, msg_id,
            )
            _pgmq_archive(queue.name, msg_id)
        else:
            backoff = _retry_backoff_seconds(read_ct)
            log.info(
                "queue %s: task %s scheduled for retry in %ds (msg=%s)",
                queue.name, task_name, backoff, msg_id,
            )
            _pgmq_set_vt(queue.name, msg_id, backoff)
        return

    _pgmq_delete(queue.name, msg_id)


# --------------------------------------------------------------------------- #
# Periodic scheduler                                                          #
# --------------------------------------------------------------------------- #


def _run_periodic_scheduler(queue: TaskQueue) -> None:
    """Leader-elected periodic scheduler.

    Tries to acquire a per-queue advisory lock on a long-lived
    connection; if another replica holds it, sleeps and retries. While
    leader, fires periodic tasks based on persisted ``cron_state``.
    Exits when the stop event is set or the connection drops.
    """
    while not _stop_event.is_set():
        conn = _try_acquire_scheduler_leadership(queue.name)
        if conn is None:
            log.info(
                "queue %s: another scheduler holds the lock; idle %ds",
                queue.name, _LEADER_RETRY_SECONDS,
            )
            if _stop_event.wait(_LEADER_RETRY_SECONDS):
                return
            continue
        try:
            log.info(
                "queue %s: periodic scheduler is leader — %d task(s)",
                queue.name, len(queue.periodic),
            )
            _scheduler_loop(queue)
        finally:
            try:
                conn.close()
            except Exception:
                log.exception("queue %s: scheduler conn close failed", queue.name)


def _try_acquire_scheduler_leadership(queue_name: str) -> Connection | None:
    """Open a dedicated connection and try ``pg_try_advisory_lock``.

    Returns the connection (with the lock held) if acquired, ``None``
    otherwise. The lock is released when the connection is closed —
    that's why we hold the connection open for the lifetime of
    leadership instead of using the per-call session helper.
    """
    engine = get_engine()
    conn = engine.connect()
    try:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"),
            {"k": _SCHEDULER_LOCK_PREFIX + queue_name},
        ).scalar()
        # Commit the implicit transaction so the lock is held at session
        # scope (released only on disconnect), not transaction scope.
        conn.commit()
    except Exception:
        log.exception("queue %s: advisory lock attempt failed", queue_name)
        conn.close()
        return None
    if not got:
        conn.close()
        return None
    return conn


def _scheduler_loop(queue: TaskQueue) -> None:
    """Compute next-fire per task, sleep, fire when due, persist last-fired."""
    next_fires: list[datetime] = [
        _initial_next_fire(queue.name, e) for e in queue.periodic
    ]

    while not _stop_event.is_set():
        soonest = min(next_fires)
        sleep_s = max(0.0, (soonest - _now()).total_seconds())
        if _stop_event.wait(timeout=sleep_s):
            return

        now = _now()
        for i, entry in enumerate(queue.periodic):
            if next_fires[i] > now:
                continue
            try:
                queue.enqueue(entry.task_name, args=(), kwargs={})
                _record_cron_fire(queue.name, entry.task_name, now)
                log.info("queue %s: periodic %s enqueued", queue.name, entry.task_name)
            except QueueFullError:
                log.warning(
                    "queue %s: periodic %s skipped — queue full; will retry next tick",
                    queue.name, entry.task_name,
                )
            except Exception:
                log.exception(
                    "queue %s: periodic %s enqueue failed",
                    queue.name, entry.task_name,
                )
            # Always advance from ``now`` (not from the missed fire time)
            # so a long downtime doesn't queue up a stampede of catch-ups —
            # we fire once and resume the normal cadence.
            next_fires[i] = croniter(entry.cron_spec, now).get_next(datetime)


def _initial_next_fire(queue_name: str, entry: _PeriodicEntry) -> datetime:
    """Compute the next fire for an entry, honoring last-fired persistence.

    If the previous run died and the cron came due during downtime,
    ``croniter(spec, last_fired).get_next()`` returns a time in the past
    — the scheduler loop fires it on the next tick (single catch-up
    fire) instead of silently skipping it.
    """
    last = _read_last_fired(queue_name, entry.task_name)
    base = last or _now()
    return croniter(entry.cron_spec, base).get_next(datetime)


def _read_last_fired(queue_name: str, task_name: str) -> datetime | None:
    with session() as s:
        row = s.execute(
            text(
                "SELECT last_fired_at FROM cron_state "
                "WHERE queue_name = :q AND task_name = :t"
            ),
            {"q": queue_name, "t": task_name},
        ).first()
    if not row or not row[0]:
        return None
    raw = row[0]
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("queue %s: bad last_fired_at %r — ignoring", queue_name, raw)
        return None


def _record_cron_fire(queue_name: str, task_name: str, fired_at: datetime) -> None:
    iso = fired_at.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    with session() as s:
        s.execute(
            text(
                "INSERT INTO cron_state (queue_name, task_name, last_fired_at) "
                "VALUES (:q, :t, :ts) "
                "ON CONFLICT (queue_name, task_name) DO UPDATE "
                "SET last_fired_at = EXCLUDED.last_fired_at"
            ),
            {"q": queue_name, "t": task_name, "ts": iso},
        )


# --------------------------------------------------------------------------- #
# pgmq helpers — small SQL surface, run via session.execute(text())           #
# --------------------------------------------------------------------------- #


def _pgmq_read(queue: str, *, vt: int, qty: int) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.execute(
            text(
                "SELECT msg_id, read_ct, enqueued_at, vt, message "
                "FROM pgmq.read(:q, :vt, :qty)"
            ),
            {"q": queue, "vt": vt, "qty": qty},
        ).mappings().all()
        return [dict(r) for r in rows]


def _pgmq_delete(queue: str, msg_id: int) -> None:
    with session() as s:
        s.execute(
            text("SELECT pgmq.delete(:q, CAST(:m AS bigint))"),
            {"q": queue, "m": msg_id},
        )


def _pgmq_archive(queue: str, msg_id: int) -> None:
    with session() as s:
        s.execute(
            text("SELECT pgmq.archive(:q, CAST(:m AS bigint))"),
            {"q": queue, "m": msg_id},
        )


def _pgmq_set_vt(queue: str, msg_id: int, vt_offset_seconds: int) -> None:
    """Push the message's visibility-timeout out by ``vt_offset_seconds``."""
    with session() as s:
        s.execute(
            text("SELECT pgmq.set_vt(:q, CAST(:m AS bigint), :vt)"),
            {"q": queue, "m": msg_id, "vt": vt_offset_seconds},
        )
