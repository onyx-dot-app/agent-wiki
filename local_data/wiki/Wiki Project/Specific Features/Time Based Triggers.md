# Time Based Triggers

A trigger has two flavors (`Trigger.kind` in `backend/app/db/models.py`):

* `kind="delta"` — fires on a wiki commit. Fan-out runs from
  `fan_out_trigger_eval` after `commit_file`. Covered in detail under
  `Auth and Permissions.md` and the trigger fan-out code.
* `kind="schedule"` — fires on the clock. The user picks how often
  (cron) and in what timezone; on each tick the same NL "if/then" gate
  delta uses runs against the **current** wiki state (no diff).

This doc covers the schedule flavor end-to-end: how the user defines
one, where the data lives, how the periodic evaluator picks the right
triggers to evaluate, and how a fire becomes an event-log row.

## When to use a schedule trigger

A schedule trigger answers the question *"is some condition true right
now in the wiki?"* on a recurring cadence. Examples:

* "Every morning at 09:00 UTC, if any project's status is still
  `red`, post a reminder to the event log."
* "Every 6 hours, if the on-call rotation page hasn't been updated in
  more than 2 weeks, fire."
* "Weekly on Mondays at 10:00, if no entry was added to the changelog
  last week, ping the team."

If the question is *"did something change?"* — that's a delta trigger.
Schedule fires on a tick; delta fires on a commit.

## Storage

The user-authored definition lives in the wiki repo (YAML), the same
way delta triggers do. A schedule trigger's YAML carries three extra
fields:

```yaml
id: trg_a1b2c3d4e5f6
owner_user_id: usr_…
scope_path: Engineering
kind: schedule
nl_description: "the on-call rotation has not been updated in two weeks"
message: "ping #ops"
destination: event_log
enabled: true
created_at: "2026-05-09T22:13:04+00:00"
schedule_cron: "0 9 * * 1"
schedule_timezone: "America/Los_Angeles"
schedule_start_at: "2026-06-01T00:00:00+00:00"  # optional
```

Postgres mirrors this in `triggers` (cache for fan-out lookup +
id→path resolution). Three columns added in migration `0008`:

| column | purpose |
|---|---|
| `schedule_timezone` | IANA name; cron is interpreted here |
| `schedule_start_at` | optional UTC ISO 8601 — eval skips ticks before this moment |
| `schedule_last_fired_at` | UTC ISO 8601 of the most recent eval; advances on every tick |

`schedule_last_fired_at` is **DB-only**. Persisting it to YAML would
mean a wiki commit on every fire, which would defeat the read-only
property of the triggers queue (and bloat history). It's runtime
state, not part of the trigger's source-of-truth definition.

## The drumbeat

The 5-minute heartbeat lives where every other periodic task does —
the in-process scheduler attached to `triggers_queue` (see
`Background Tasks.md` for the queue/scheduler split). One line in
`backend/app/tasks/periodic.py`:

```python
@triggers_queue.periodic_task(crontab(minute="*/5"))
def evaluate_scheduled_triggers() -> None:
    evaluate_due_schedule_triggers(datetime.now(timezone.utc))
```

That's it. The scheduler holds an advisory lock so only one replica
fires it; if the leader dies, another picks it up at the next
heartbeat. `cron_state` records last-fired-at per task so a restart
doesn't double-fire or skip.

The 5-min granularity is a deliberate ceiling — a `*/15 * * * *`
trigger is observable to the user as a 15-minute cadence, not 5
minutes. Don't tighten this without thinking about queue depth: every
fire builds the wiki snapshot once and runs at least one LLM call per
matching trigger.

## Selection: `find_due_schedule_triggers`

Each tick loads enabled `kind="schedule"` triggers and asks croniter
whether each is due. Logic in
`backend/app/triggers/engine.py:find_due_schedule_triggers`:

```python
tz = ZoneInfo(t.schedule_timezone)
base = _schedule_base(t).astimezone(tz)
next_fire_local = croniter(t.schedule_cron, base).get_next(datetime)
next_fire_utc = next_fire_local.astimezone(timezone.utc)
if t.schedule_start_at and next_fire_utc < parse(t.schedule_start_at):
    return False        # held by the start-at anchor
return next_fire_utc <= now_utc
```

`_schedule_base(t)` picks the croniter base in priority order:

1. `max(schedule_last_fired_at, schedule_start_at)` if either is set
2. otherwise `created_at`

That order matters. Once a trigger has fired, `last_fired_at` is the
only relevant floor — croniter advances from there. `start_at` keeps
the trigger quiet until the anchor passes (and stays the floor
afterwards in case the user bumps it forward). `created_at` is the
fallback for a brand-new trigger with no anchor and no fire history,
so the first cron match after creation is the first fire — we don't
backfire historical matches.

### Missed ticks during downtime

If the worker was down for an hour and a `* * * * *` trigger has 60
unfired matches, `find_due_schedule_triggers` returns it once. The
evaluator advances `last_fired_at` to *the current `now`*, not to the
next un-fired cron match, so subsequent ticks see only the next
pending fire. **One catch-up fire per trigger per restart, never a
stampede.** This is the same semantic the periodic scheduler in
`queue.py` uses for its own crons.

### DST

Cron is interpreted in the trigger's `schedule_timezone`. We
`base.astimezone(ZoneInfo(tz))` before handing to croniter, then
convert the result back to UTC. `ZoneInfo` honors DST transitions
correctly, so a `0 9 * * *` trigger in `America/Los_Angeles` fires at
the local 09:00 across the spring-forward and fall-back boundaries
without drift.

## Evaluation

For each due trigger, the periodic body
(`evaluate_due_schedule_triggers` in `backend/app/tasks/triggers.py`)
runs the same shape as the delta path, with three differences:

```
                     delta                          schedule
                     -----                          --------
payload              snapshot + diff view           snapshot + SCHEDULED CHECK block
LLM gate             nl_matches  (diff prompt)      nl_matches_snapshot (state prompt)
on match             nl_render_message              nl_render_snapshot_message
fire metadata        change_kind="edit"|"create"    change_kind="schedule", sha=""
```

The wiki snapshot is built **once per tick** and reused across every
matching trigger. Each trigger pays its own per-call eval (and
render-on-match). The snapshot's per-doc and total budgets in
`app/triggers/diff.py` apply unchanged.

### Owner ACL re-check

Same invariant as delta: the owner's read access on `scope_path` is
re-checked at fire-time. A trigger created when the owner had access
and then revoked must not produce a rendered message that embeds doc
body excerpts. `_owner_can_read_scope` in `app/tasks/triggers.py`
mirrors the delta path's inline check.

### Why a separate prompt?

The delta `nl_matches` prompt explicitly tells the model to evaluate
**the diff**, treating the wiki snapshot as context. For schedule
there is no diff — only state. Reusing the prompt would mislead the
model. The snapshot variants
(`matches_snapshot` / `render_snapshot_message` in
`app/triggers/natural_language.py`) say:

> Evaluate the trigger description against the **current state** of
> the wiki, focusing on the document(s) under the listed scope. There
> is no diff: this is a state check, not a change check.

This keeps the user-facing "if/then" UX identical between the two
flavors — the wording in `nl_description` doesn't change.

## Always advance `last_fired_at`

After evaluating a due trigger — match, no-match, or even an exception
during eval — the body calls
`triggers_repo.record_schedule_fire(trigger.id, now_iso)`:

```python
try:
    fired = _evaluate_one_schedule(trigger, ...)
finally:
    triggers_repo.record_schedule_fire(trigger.id, now_iso)
```

The `finally` is load-bearing. If we only advanced on match, a
no-match tick would leave `last_fired_at` stale, croniter would keep
returning the same now-past fire, and the next tick would re-evaluate
the same window. Same hazard for a transient LLM error. Always
advancing keeps the evaluator idempotent across ticks.

## What lands in the event log

A matched schedule fire writes one `trigger.fire` row via the same
`_record_fire` helper the delta path uses:

```json
{
  "trigger_id": "trg_a1b2c3d4e5f6",
  "doc_path": "Engineering",
  "sha": "",
  "change_kind": "schedule",
  "reason": "the on-call rotation page was last edited 17 days ago",
  "message": "ping #ops",
  "message_instruction": "ping #ops",
  "destination": "event_log"
}
```

`sha=""` because there's no commit; `doc_path` is the trigger's
`scope_path` (whatever the user is watching — a doc, a folder, or the
root). Downstream consumers that filter by `change_kind` get a clean
schedule-vs-delta split. Outbound dispatch (anything other than
`event_log`) is still a TODO — fires to other destinations log a
warning and write to events anyway, same as delta.

## API surface

`POST /api/triggers` and `PUT /api/triggers/<id>` accept the schedule
fields directly:

```json
{
  "scope_path": "Engineering",
  "nl_description": "...",
  "message": "...",
  "kind": "schedule",
  "schedule_cron": "0 9 * * 1",
  "schedule_timezone": "America/Los_Angeles",
  "schedule_start_at": "2026-06-01T00:00:00Z"
}
```

Validation is in `backend/app/triggers/repo.py:_validate_schedule_fields`
— `croniter.is_valid` for the cron, `ZoneInfo(...)` for the tz,
`datetime.fromisoformat` for the anchor. Invalid values surface as
HTTP 400 via the existing `try/except ValueError` block in
`backend/app/api/triggers.py`.

The kind is **immutable** on update — switching delta ↔ schedule
means delete + recreate. The form enforces this with a disabled
selector in edit mode; the API doesn't accept `kind` in
`UpdateTriggerRequest`.

`GET /api/triggers/<id>/version/<sha>` returns the schedule fields as
they existed at that commit, so the "edit from history" flow in the
UI round-trips them.

## Frontend

`frontend/src/components/triggers/TriggerModal.tsx` exposes:

* **Frequency preset** dropdown — every 15 min / every 30 min /
  hourly / every 6 hours / daily / weekly / monthly / custom. The
  preset writes a cron string under the hood. Daily/weekly/monthly
  show a time-of-day picker; weekly adds a day-of-week select;
  monthly adds a day-of-month input.
* **Timezone** select — populated from
  `Intl.supportedValuesOf("timeZone")`, defaulting to
  `Intl.DateTimeFormat().resolvedOptions().timeZone` (browser TZ).
* **Do not fire before** — `<input type="datetime-local">`,
  interpreted as the browser's local clock and converted to UTC ISO
  on submit. Optional.
* **Advanced** disclosure — raw 5-field cron with per-field labels
  (Minute / Hour / Day of month / Month / Day of week). Editing it
  switches the preset to "custom".

A live summary line shows the cron in human form
(`describeCron(cron, tz)` from `frontend/src/lib/cron.ts`) and the
literal cron expression that will be saved. The triggers list page
renders schedule rows with a `SCHEDULE` badge and a `WHEN …` line —
same `describeCron` helper, plus the start-at and last-fired
timestamps when present.

## Files

```
backend/app/
├── db/models.py                                Trigger schedule_* columns
├── db/migrations/versions/0008_*.py            ADD COLUMN migration
├── triggers/repo.py                            kind allowlist + validation
├── triggers/storage.py                         YAML serialize/parse
├── triggers/engine.py                          find_due_schedule_triggers, base picker
├── triggers/diff.py                            build_schedule_payload
├── triggers/natural_language.py                matches_snapshot + render_snapshot_message
├── triggers/time_based.py                      thin due_triggers wrapper
├── tasks/periodic.py                           @periodic_task heartbeat
├── tasks/triggers.py                           evaluate_due_schedule_triggers
├── api/triggers.py                             accepts kind=schedule
└── models/trigger.py                           schedule_* fields on req/view models

frontend/src/
├── lib/cron.ts                                 preset ↔ cron + describeCron + tz helpers
├── components/triggers/TriggerModal.tsx        kind selector + schedule fieldset
├── app/triggers/page.tsx                       SCHEDULE badge + WHEN row
├── lib/triggers.ts                             types include schedule fields
└── types/index.ts                              re-exports from lib/triggers.ts
```

## Tests

`backend/tests/test_schedule_triggers.py` — 15 cases:

* **validation** — bad cron / bad tz / bad start_at / missing fields
  for `kind=schedule`, schedule fields rejected for `kind=delta`,
  YAML round-trip persists everything.
* **`find_due_schedule_triggers`** — excludes delta, excludes
  disabled, skips before `start_at`, includes when due after
  `last_fired_at`, skips when the next fire is still in the future.
* **evaluator** — fires on match (writes the right `trigger.fire`
  payload, advances `last_fired_at`), advances `last_fired_at` even
  on no-match, skips when the owner lacks read on the scope.
* **`rebuild_from_filesystem`** — schedule YAML round-trips through
  the cache rebuild, columns hydrate correctly.

The LLM seam is patched at `app.triggers.natural_language.complete`,
not at the SDK — same pattern as the delta tests.

## What's not built yet

* **Outbound dispatch.** All fires currently land in the event log.
  When we wire up the destinations catalog (Slack, webhooks, etc.),
  schedule fires get the same path delta fires do — same `_record_fire`
  helper.
* **Sub-5-minute resolution.** The heartbeat is 5 min. A
  `*/1 * * * *` schedule trigger is valid but observable as a 5-min
  cadence. Tighten the heartbeat first if/when sub-minute matters.
* **In-form "next 3 fires" preview.** The form shows the cron
  literally and a human description; computing the next-N fires
  client-side would need a JS cron iterator. Not blocking.
