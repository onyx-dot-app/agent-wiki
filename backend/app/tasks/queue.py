"""Task queue backed by Redis Streams.

The decorator-shaped API the rest of the app uses:

  * ``@queue.task()``                              — register a handler
  * ``@queue.periodic_task(crontab(minute="*/5"))`` — register a cron handler
  * ``task(*args, **kwargs)``                      — direct call → enqueue
  * ``task.schedule(args=(...), eta=dt)``          — enqueue with a delay/eta
  * ``queue.depth()``                              — ready/delayed/in_flight counts
  * ``queue.immediate = True``                     — synchronous mode for tests
  * ``QueueFullError``                             — raised when at the cap

Each named ``TaskQueue`` persists its messages in two Redis data structures:

* ``queue:{name}`` — Redis Stream for ready messages. Consumer group
  ``workers`` tracks which messages are in-flight.
* ``queue:{name}:delay`` — Sorted set, score = fire timestamp (ms since
  epoch). Messages fire when score ≤ now. Delayed sends (``eta`` /
  ``delay``) land here first; a background pump thread moves them to the
  stream once they're due.
* ``queue:{name}:bodies`` — Hash, msg_id → JSON body. Stores the payload
  for delayed messages until the pump moves them to the stream.
* ``queue:{name}:msg_counter`` — INCR counter that generates monotonic
  integer msg_ids for delayed messages.

Worker semantics (see ``run_consumer``): ``concurrency`` worker threads
each long-poll the stream via XREADGROUP. Handlers run in-thread. On
exception we re-enqueue the message with exponential backoff (via the
delay sorted set). Messages that fail more than ``MAX_RETRIES`` times are
dropped with a log warning.

Periodic tasks are run by an in-process scheduler thread that holds a
per-queue Redis lock — only one process across the deployment fires crons
for a given queue. ``cron_state`` (a Postgres table) records each task's
last-fired timestamp so a restart picks up where the previous process left
off.

Shutdown: SIGTERM / SIGINT sets a stop event; worker threads finish their
current task and exit. The pump thread exits too.
"""
from __future__ import annotations

import json
import logging
import signal
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Generator, cast

import redis as redis_lib
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

# Imported as a module (not ``from app.config import CONFIG``) so every
# read sees the live attribute — tests patch ``app.config.CONFIG``.
import app.config
from app.db.session import session

log = logging.getLogger(__name__)


_DEFAULT_VT_SECONDS = 300       # visibility timeout — how long a worker holds a message
_POLL_IDLE_SLEEP = 1.0          # seconds to sleep when the stream is empty
_MAX_RETRIES = 3
# An entry claimed this many times without being acked is dropped as poison:
# each delivery means a consumer took it and died/stalled before finishing, so
# handing it to a fourth consumer would just wedge that one too.
_MAX_DELIVERIES = 3
_RECLAIM_BATCH = 10             # stale entries adopted per idle reclaim pass
# Reclaim fires at vt * factor, not vt itself. Reclaiming an entry whose
# consumer is merely *slow* (not dead) runs it twice, and several handlers have
# non-idempotent side effects (trigger notifications, wiki commits) — so the
# reclaim horizon sits well past any handler's legitimate runtime while still
# recovering orphans within minutes. 3 x 300s = 15 min.
_RECLAIM_IDLE_FACTOR = 3
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 600
_LEADER_RETRY_SECONDS = 30
_PUMP_INTERVAL = 0.5            # how often the pump thread checks for due delayed messages
_SCHED_LOCK_TTL_MS = 65_000     # Redis TTL for the scheduler leadership key


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
# the pump and scheduler. Module-global so test fixtures don't have to thread #
# it around.                                                                  #
# --------------------------------------------------------------------------- #


_stop_event = threading.Event()


def install_signal_handlers() -> None:
    """Public entry point: install SIGTERM/SIGINT handlers that set the shared
    stop event. Call once from a process's main thread (e.g. ``run_worker``)
    before spawning consumer threads. A no-op off the main thread, so
    ``run_consumer`` calling it from a worker thread is harmless."""
    _install_signal_handlers()


def request_shutdown() -> None:
    """Set the shared stop event so every consumer/pump/scheduler in this
    process drains and exits. Used when one consumer dies unexpectedly so its
    siblings stop too and the process can exit (and be restarted)."""
    _stop_event.set()


def _install_signal_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def _on_signal(signo: int, _frame: Any) -> None:
        log.info("queue: signal %d received — initiating graceful shutdown", signo)
        _stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass


# --------------------------------------------------------------------------- #
# Redis client — lazy singleton                                               #
# --------------------------------------------------------------------------- #


_redis_client: Any = None
_redis_lock = threading.Lock()


def get_redis() -> Any:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        _redis_client = redis_lib.from_url(app.config.CONFIG.redis_url, decode_responses=True)
        return _redis_client


def reset_redis_for_tests() -> None:
    """Drop the cached client so the next ``get_redis()`` re-reads ``CONFIG``.

    Tests point ``CONFIG.redis_url`` at a per-test broker; without this the
    lazily-cached client from an earlier test (or the default URL) leaks across
    cases. Mirrors ``reset_engine_for_tests`` / ``fts.reset_client_for_tests``.
    """
    global _redis_client
    _redis_client = None


# --------------------------------------------------------------------------- #
# Redis key helpers                                                           #
# --------------------------------------------------------------------------- #


def _prefix() -> str:
    return app.config.CONFIG.redis_key_prefix


def _stream_key(name: str) -> str:
    return f"{_prefix()}queue:{name}"


def _delay_key(name: str) -> str:
    return f"{_prefix()}queue:{name}:delay"


def _bodies_key(name: str) -> str:
    return f"{_prefix()}queue:{name}:bodies"


def _counter_key(name: str) -> str:
    return f"{_prefix()}queue:{name}:msg_counter"


def _sched_lock_key(name: str) -> str:
    return f"{_prefix()}queue:{name}:sched_lock"


def _as_str(v: Any) -> str:
    """Redis values are str under our decode_responses client, but be robust to a
    non-decoding client (bytes) so id parsing can't silently mis-read."""
    return v.decode() if isinstance(v, bytes) else str(v)


# --------------------------------------------------------------------------- #
# Consumer-group bootstrap                                                    #
# --------------------------------------------------------------------------- #


def _ensure_consumer_group(queue_name: str) -> None:
    """Create the stream + consumer group if they don't exist yet.

    Uses ``XGROUP CREATE ... MKSTREAM`` so the stream is created even if
    no messages have been enqueued yet. The ``0`` start-id means the group
    will process messages from the beginning on first start.
    """
    r = get_redis()
    try:
        r.xgroup_create(_stream_key(queue_name), "workers", id="0", mkstream=True)
    except Exception as exc:
        # BUSYGROUP = group already exists — normal on restart.
        if "BUSYGROUP" not in str(exc):
            raise


# --------------------------------------------------------------------------- #
# Task queue + handler                                                        #
# --------------------------------------------------------------------------- #


class QueueDepth(BaseModel):
    """Snapshot of message counts, broken down by state."""

    ready: int
    delayed: int
    in_flight: int

    @property
    def pending(self) -> int:
        return self.ready + self.delayed


class _PeriodicEntry(BaseModel):
    cron_spec: str
    task_name: str


class TaskQueue(BaseModel):
    """One named queue + handler registry. Backed by Redis Streams."""

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
        """Run handlers synchronously inside the ``with`` block; restore on exit."""
        prev = self.immediate
        self.immediate = True
        try:
            yield self
        finally:
            self.immediate = prev

    # ----- depth + enqueue --------------------------------------------------

    def depth(self) -> "QueueDepth":
        """Per-state message counts.

        ``ready`` = stream length (includes in-flight, which is a small
        subset; XPENDING is approximate anyway).
        ``delayed`` = items in the delay sorted set not yet moved to stream.
        ``in_flight`` = best-effort XPENDING count (0 when group not yet set up).

        Returns all-zeros in immediate mode.
        """
        if self.immediate:
            return QueueDepth(ready=0, delayed=0, in_flight=0)
        r = get_redis()
        try:
            stream_len = int(r.xlen(_stream_key(self.name)) or 0)
            delayed = int(r.zcard(_delay_key(self.name)) or 0)
            try:
                pending_info = r.xpending(_stream_key(self.name), "workers")
                in_flight = int(pending_info.get("pending", 0))
            except Exception:
                in_flight = 0
            ready = max(0, stream_len - in_flight)
            return QueueDepth(ready=ready, delayed=delayed, in_flight=in_flight)
        except Exception:
            return QueueDepth(ready=0, delayed=0, in_flight=0)

    def oldest_age_seconds(self) -> float | None:
        """Seconds the oldest message in the ready stream has waited, or None if
        nothing is ready. Redis stream IDs embed the enqueue time (ms), so the
        oldest ready entry's id dates the oldest unworked message — the backlog
        signal depth alone misses when the queue is shallow. Starts strictly
        after the group's last-delivered-id, so an in-flight entry (delivered to
        a worker, not yet acked) isn't counted as queue latency — matching
        depth().ready. Uses Redis server time to match the id clock. Ignores the
        delay set (not-yet-due scheduled items). None in immediate mode."""
        if self.immediate:
            return None
        r = get_redis()
        try:
            # Everything up to last-delivered-id has already been handed to a
            # worker; the oldest *ready* message is the next one after it.
            last_id = "0-0"
            try:
                for g in r.xinfo_groups(_stream_key(self.name)):
                    if _as_str(g.get("name")) == "workers":
                        last_id = _as_str(g.get("last-delivered-id")) or "0-0"
                        break
            except Exception:
                pass  # group not created yet → treat every entry as ready
            entries = r.xrange(_stream_key(self.name), min=f"({last_id}", count=1)
            if not entries:
                return None
            enqueued_ms = int(_as_str(entries[0][0]).split("-", 1)[0])
            secs, micros = r.time()
            now_ms = int(secs) * 1000 + int(micros) // 1000
            return max(0.0, (now_ms - enqueued_ms) / 1000.0)
        except Exception:
            return None

    def enqueue(
        self,
        task_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        eta: datetime | None = None,
        delay: int | None = None,
    ) -> int | None:
        """Enqueue a task. Returns the integer msg_id, or ``None`` in immediate mode."""
        if self.immediate:
            handler = self.handlers[task_name]
            handler(*args, **(kwargs or {}))
            return None

        delay_seconds = _resolve_delay(eta, delay)
        body = {
            "task": task_name,
            "args": list(args),
            "kwargs": dict(kwargs or {}),
            "retry_count": 0,
        }

        r = get_redis()

        # Cap check + enqueue. Not atomic across processes, but close enough:
        # the cap is a soft guard against runaway producers, not a hard limit.
        total = int(r.xlen(_stream_key(self.name)) or 0) + int(
            r.zcard(_delay_key(self.name)) or 0
        )
        if total >= self.max_size:
            raise QueueFullError(self.name, total, self.max_size)

        msg_id = int(r.incr(_counter_key(self.name)))

        if delay_seconds <= 0:
            # Immediate: push directly onto stream. Embed msg_id in body so
            # the worker can log/track it.
            body["msg_id"] = msg_id
            r.xadd(_stream_key(self.name), {"payload": json.dumps(body)})
        else:
            fire_ts = int(_now().timestamp() * 1000) + delay_seconds * 1000
            body["msg_id"] = msg_id
            r.hset(_bodies_key(self.name), str(msg_id), json.dumps(body))
            r.zadd(_delay_key(self.name), {str(msg_id): fire_ts})

        return msg_id


class Task(BaseModel):
    """Wrapper around a registered handler. Calling it enqueues."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    queue: TaskQueue
    name: str
    fn: Callable[..., Any]

    def __call__(self, *args: Any, **kwargs: Any) -> int | None:
        return self.queue.enqueue(self.name, args, kwargs)

    def schedule(
        self,
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        eta: datetime | None = None,
        delay: int | None = None,
    ) -> int | None:
        """Enqueue with a future ``eta`` or relative ``delay`` (seconds).

        Returns the integer msg_id so callers that want to cancel the
        scheduled fire later can pass it to ``cancel_delayed_message``.
        """
        return self.queue.enqueue(
            self.name, tuple(args), kwargs or {}, eta=eta, delay=delay
        )


# --------------------------------------------------------------------------- #
# Cancellation of delayed messages                                            #
# --------------------------------------------------------------------------- #


def cancel_delayed_message(queue_name: str, msg_id: int) -> None:
    """Cancel a delayed message that hasn't fired yet.

    No-op if the message has already been moved to the stream (it will
    run and the handler's own stale check should discard it), or if the
    id doesn't exist.
    """
    r = get_redis()
    try:
        r.zrem(_delay_key(queue_name), str(msg_id))
        r.hdel(_bodies_key(queue_name), str(msg_id))
    except Exception:
        log.exception("queue %s: cancel_delayed_message %s failed", queue_name, msg_id)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _resolve_delay(eta: datetime | None, delay: int | None) -> int:
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


def _retry_backoff_seconds(retry_count: int) -> int:
    delay = _RETRY_BASE_SECONDS * (2 ** max(0, retry_count - 1))
    return min(_RETRY_MAX_SECONDS, delay)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Pump thread — moves due delayed messages onto the stream                   #
# --------------------------------------------------------------------------- #


def _run_pump(queues: list[TaskQueue]) -> None:
    """Background thread that polls the delay sorted sets and moves due
    messages onto their respective streams. One thread serves all queues
    in the process."""
    queue_names = [q.name for q in queues]
    log.debug("pump: started for queues %s", queue_names)
    while not _stop_event.is_set():
        _stop_event.wait(_PUMP_INTERVAL)
        if _stop_event.is_set():
            break
        now_ts = int(_now().timestamp() * 1000)
        r = get_redis()
        for name in queue_names:
            try:
                due = r.zrangebyscore(_delay_key(name), "-inf", now_ts)
                for msg_id_str in due:
                    body_json = r.hget(_bodies_key(name), msg_id_str)
                    if body_json:
                        r.xadd(_stream_key(name), {"payload": body_json})
                    # Remove from delay set + bodies regardless of whether
                    # the body was found (could have been already canceled).
                    r.zrem(_delay_key(name), msg_id_str)
                    r.hdel(_bodies_key(name), msg_id_str)
            except Exception:
                log.exception("pump: error processing queue %s", name)
    log.debug("pump: stopped")


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

    Each thread reads from the Redis Stream via XREADGROUP and runs the
    handler in-thread. The pump thread is started once per process (it
    serves all queues). Returns when SIGTERM / SIGINT is received.
    """
    _install_signal_handlers()
    _ensure_consumer_group(queue.name)
    log.info(
        "queue %s: consumer up (concurrency=%d, vt=%ds)",
        queue.name, concurrency, vt_seconds,
    )

    # Start the pump thread once per queue consumer (it's idempotent — the
    # pump only processes the queues it's given, so running one pump per
    # consumer is fine; they won't double-process because ZRANGEBYSCORE +
    # ZREM isn't atomic, but duplicate stream inserts just add idempotent
    # messages that the handler's stale check absorbs).
    threading.Thread(
        target=_run_pump,
        args=([queue],),
        name=f"pump-{queue.name}",
        daemon=True,
    ).start()

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

    log.info("queue %s: consumer drained, exiting", queue.name)


def _worker_loop(
    queue: TaskQueue, worker_id: int, vt_seconds: int, max_retries: int
) -> None:
    consumer_name = f"worker-{worker_id}-{uuid.uuid4().hex[:8]}"
    log.debug("queue %s: worker %s ready", queue.name, consumer_name)
    r = get_redis()
    block_ms = int(_POLL_IDLE_SLEEP * 1000)

    while not _stop_event.is_set():
        try:
            results = r.xreadgroup(
                "workers",
                consumer_name,
                {_stream_key(queue.name): ">"},
                count=1,
                block=block_ms,
            )
        except Exception:
            log.exception("queue %s: xreadgroup failed", queue.name)
            _stop_event.wait(_POLL_IDLE_SLEEP)
            continue

        if not results:
            _reclaim_stale(queue, consumer_name, r, vt_seconds, max_retries)
            continue

        try:
            for _stream, messages in results:
                for stream_entry_id, fields in messages:
                    _handle_entry(queue, stream_entry_id, fields, r, max_retries)
        except Exception:
            # _process_one contains handler failures, but anything escaping it
            # (a Redis error on ack, a malformed message shape) must not kill
            # this thread: its exception would land in a Future nobody reads,
            # and the queue would silently stop consuming until pod restart.
            log.exception("queue %s: worker loop error — continuing", queue.name)

    log.debug("queue %s: worker %s stopped", queue.name, consumer_name)


def _handle_entry(
    queue: TaskQueue, stream_entry_id: str, fields: dict[str, Any], r: Any, max_retries: int
) -> None:
    payload_json = fields.get("payload", "{}")
    try:
        body: dict[str, Any] = json.loads(payload_json)
    except (ValueError, TypeError):
        log.error(
            "queue %s: malformed payload at %s — acking and skipping",
            queue.name, stream_entry_id,
        )
        _drop_entry(queue, r, stream_entry_id)
        return
    _process_one(queue, stream_entry_id, body, r, max_retries=max_retries)



def _drop_entry(queue: TaskQueue, r: Any, stream_entry_id: str) -> None:
    """Remove a finished (or discarded) entry: XDEL first, then XACK.

    The failure modes between the two calls are asymmetric. Delete-then-ack
    failing halfway leaves a dangling PEL reference to a deleted entry, which
    the next XAUTOCLAIM pass cleans up (Redis returns such entries with no
    fields; the reclaim loop skips them). Ack-then-delete failing halfway
    leaves an acked entry in the stream forever — never deliverable again,
    invisible to reclaim, but still counted by depth().ready and pinning
    oldest_age_seconds(): a zombie backlog that cannot drain.
    """
    r.xdel(_stream_key(queue.name), stream_entry_id)
    r.xack(_stream_key(queue.name), "workers", stream_entry_id)


def _reclaim_stale(
    queue: TaskQueue, consumer_name: str, r: Any, vt_seconds: int, max_retries: int
) -> None:
    """Adopt pending entries whose consumer died or stalled past ``vt_seconds``.

    ``XREADGROUP ">"`` never re-reads another consumer's pending entries, and
    every worker start mints a fresh consumer name — so an entry in flight when
    its worker was killed would otherwise sit in the PEL forever, invisible to
    all future consumers. Runs only when this consumer is idle, so a busy
    single-consumer queue never steals its own in-flight work.

    Reclaim makes delivery at-least-once: a handler still running past the
    reclaim horizon (``vt_seconds * _RECLAIM_IDLE_FACTOR``) while another
    consumer is idle runs twice. Entries delivered more than
    ``_MAX_DELIVERIES`` times are dropped as poison rather than wedging every
    successive consumer. Never raises — this runs on the consumer thread's
    idle path, outside the message-handling guard.
    """
    sk = _stream_key(queue.name)
    try:
        resp = r.xautoclaim(
            sk, "workers", consumer_name,
            min_idle_time=vt_seconds * _RECLAIM_IDLE_FACTOR * 1000,
            start_id="0-0", count=_RECLAIM_BATCH,
        )
    except Exception:
        log.exception("queue %s: xautoclaim failed", queue.name)
        return
    # redis-py returns (next_start_id, messages[, deleted_ids]) depending on
    # server version; index 1 is the claimed messages in every shape.
    try:
        messages = cast("list[tuple[str, dict[str, Any] | None]]", resp[1])
    except (IndexError, TypeError):
        return
    for stream_entry_id, fields in messages:
        try:
            if fields is None:
                continue  # entry was XDELed while still pending — nothing to run
            delivered = 1
            info = r.xpending_range(
                sk, "workers", min=stream_entry_id, max=stream_entry_id, count=1,
            )
            if info:
                delivered = int(info[0].get("times_delivered", 1))
            if delivered > _MAX_DELIVERIES:
                log.warning(
                    "queue %s: entry %s delivered %d times without completing — dropping as poison",
                    queue.name, stream_entry_id, delivered,
                )
                _drop_entry(queue, r, stream_entry_id)
                continue
            log.info(
                "queue %s: reclaimed stale entry %s (delivery %d)",
                queue.name, stream_entry_id, delivered,
            )
            _handle_entry(queue, stream_entry_id, fields, r, max_retries)
        except Exception:
            # One bad entry (or a Redis hiccup on its ack/delete) must not
            # abort the remaining claims or escape to kill the consumer.
            log.exception("queue %s: reclaimed entry %s failed", queue.name, stream_entry_id)


def _process_one(
    queue: TaskQueue,
    stream_entry_id: str,
    body: dict[str, Any],
    r: Any,
    *,
    max_retries: int,
) -> None:
    task_name: str = body.get("task", "")
    args: list[Any] = body.get("args") or []
    kwargs: dict[str, Any] = body.get("kwargs") or {}
    retry_count: int = int(body.get("retry_count", 0))

    handler = queue.handlers.get(task_name)
    if handler is None:
        log.error(
            "queue %s: no handler for task %r — discarding entry %s",
            queue.name, task_name, stream_entry_id,
        )
        _drop_entry(queue, r, stream_entry_id)
        return

    try:
        log.debug(
            "queue %s: run %s entry=%s retry=%d",
            queue.name, task_name, stream_entry_id, retry_count,
        )
        handler(*args, **kwargs)
    except Exception:
        log.exception(
            "queue %s: task %s failed (entry=%s retry=%d)",
            queue.name, task_name, stream_entry_id, retry_count,
        )
        # Persist the retry copy BEFORE dropping the entry. If any step after
        # the persist fails partway, the original entry is still (or again)
        # deliverable and the worst case is a duplicate retry, bounded by the
        # delivery cap — whereas drop-then-persist failing between the two
        # would leave no copy anywhere and lose the task outright.
        if retry_count < max_retries:
            backoff = _retry_backoff_seconds(retry_count + 1)
            log.info(
                "queue %s: task %s retry %d in %ds",
                queue.name, task_name, retry_count + 1, backoff,
            )
            retry_body = dict(body)
            retry_body["retry_count"] = retry_count + 1
            new_id = int(r.incr(_counter_key(queue.name)))
            retry_body["msg_id"] = new_id
            fire_ts = int(_now().timestamp() * 1000) + backoff * 1000
            r.hset(_bodies_key(queue.name), str(new_id), json.dumps(retry_body))
            r.zadd(_delay_key(queue.name), {str(new_id): fire_ts})
        else:
            log.warning(
                "queue %s: task %s exceeded %d retries — dropping entry %s",
                queue.name, task_name, max_retries, stream_entry_id,
            )
        _drop_entry(queue, r, stream_entry_id)
        return

    _drop_entry(queue, r, stream_entry_id)


# --------------------------------------------------------------------------- #
# Periodic scheduler                                                          #
# --------------------------------------------------------------------------- #


def _run_periodic_scheduler(queue: TaskQueue) -> None:
    """Leader-elected periodic scheduler using a Redis lock.

    Acquires a per-queue lock via SET NX PX; if another replica holds it,
    sleeps and retries. While leader, fires periodic tasks based on
    persisted ``cron_state``. Refreshes the lock every half-TTL so it
    doesn't expire while still leader.
    """
    while not _stop_event.is_set():
        lock_value = uuid.uuid4().hex
        if _try_acquire_scheduler_leadership(queue.name, lock_value):
            log.info(
                "queue %s: periodic scheduler is leader — %d task(s)",
                queue.name, len(queue.periodic),
            )
            try:
                _scheduler_loop(queue, lock_value)
            finally:
                _release_scheduler_lock(queue.name, lock_value)
        else:
            log.info(
                "queue %s: another scheduler holds the lock; idle %ds",
                queue.name, _LEADER_RETRY_SECONDS,
            )
            if _stop_event.wait(_LEADER_RETRY_SECONDS):
                return


def _try_acquire_scheduler_leadership(queue_name: str, lock_value: str) -> bool:
    r = get_redis()
    try:
        return bool(
            r.set(_sched_lock_key(queue_name), lock_value, nx=True, px=_SCHED_LOCK_TTL_MS)
        )
    except Exception:
        log.exception("queue %s: scheduler lock acquire failed", queue_name)
        return False


_LUA_REFRESH_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""

_LUA_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _refresh_scheduler_lock(queue_name: str, lock_value: str) -> bool:
    """Extend the TTL atomically if we still own the lock."""
    r = get_redis()
    try:
        result = r.eval(_LUA_REFRESH_LOCK, 1, _sched_lock_key(queue_name), lock_value, _SCHED_LOCK_TTL_MS)
        return bool(result)
    except Exception:
        return False


def _release_scheduler_lock(queue_name: str, lock_value: str) -> None:
    r = get_redis()
    try:
        r.eval(_LUA_RELEASE_LOCK, 1, _sched_lock_key(queue_name), lock_value)
    except Exception:
        log.exception("queue %s: scheduler lock release failed", queue_name)


def _scheduler_loop(queue: TaskQueue, lock_value: str) -> None:
    """Compute next-fire per task, sleep, fire when due, persist last-fired."""
    next_fires: list[datetime] = [
        _initial_next_fire(queue.name, e) for e in queue.periodic
    ]
    refresh_interval = _SCHED_LOCK_TTL_MS / 2 / 1000  # seconds
    last_refresh = _now()

    while not _stop_event.is_set():
        soonest = min(next_fires)
        sleep_s = max(0.0, (soonest - _now()).total_seconds())
        # Sleep in short chunks so we can refresh the lock and check stop.
        sleep_s = min(sleep_s, refresh_interval)
        if _stop_event.wait(timeout=sleep_s):
            return

        # Refresh lock to stay leader.
        if (_now() - last_refresh).total_seconds() >= refresh_interval:
            if not _refresh_scheduler_lock(queue.name, lock_value):
                log.warning("queue %s: lost scheduler lock — stepping down", queue.name)
                return
            last_refresh = _now()

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
            next_fires[i] = croniter(entry.cron_spec, now).get_next(datetime)


def _initial_next_fire(queue_name: str, entry: _PeriodicEntry) -> datetime:
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
