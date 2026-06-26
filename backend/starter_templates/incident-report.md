---
name: Incident report
description: Postmortem template — timeline, impact, root cause, and action items.
ingestion_auto_update_disabled: true
---
# Incident <YYYY-MM-DD>: <short summary>

**Status:** Investigating | Mitigated | Resolved | Postmortem complete
**Severity:** SEV-1 | SEV-2 | SEV-3 | SEV-4
**Incident commander:** <name>
**Authors:** <names>
**Customer-facing:** Yes | No

## TL;DR

Two or three sentences a busy executive can read. What broke, who was affected, how long, and what we did about it.

## Impact

- **User impact:** <what users saw or could not do>
- **Scope:** <regions, tenants, percent of traffic>
- **Duration:** <start UTC> → <end UTC> (<X hours Y minutes>)
- **Revenue or SLA impact:** <if applicable>

## Timeline

All times UTC.

| Time | Event |
| --- | --- |
| HH:MM | <event> |
| HH:MM | First alert fired |
| HH:MM | Incident declared |
| HH:MM | Mitigation applied |
| HH:MM | Full resolution |

## Detection

How did we find out? Was it the right channel, and was it fast enough? If a customer reported it before our monitoring did, say so explicitly — that is itself an action item.

## Root cause

What actually happened, at a technical level. Do not stop at "the deploy broke it" — keep asking why until you reach something actionable.

## Contributing factors

The things that made this worse than it had to be — missing alerts, stale runbooks, recent on-call rotation changes, knowledge gaps, tooling friction.

## What went well

Genuinely. Resist the urge to skip this.

-

## What went poorly

-

## Where we got lucky

The bullets that could have been "what went poorly" if a small thing had gone differently. Worth surfacing because they are usually the most fragile parts of the response.

-

## Action items

Specific, owned, dated. Tag each as **prevent** (stops recurrence), **mitigate** (reduces impact next time), or **process** (improves response).

- [ ] **[prevent]** <action> — owner: <name>, due: <date>, ticket: <link>
- [ ] **[mitigate]** <action> — owner: <name>, due: <date>, ticket: <link>
- [ ] **[process]** <action> — owner: <name>, due: <date>, ticket: <link>

## Supporting data

Include details but avoid pasting large dumps inline. Provide data for conclusions, justifications, results, etc. below.

## Lessons

A short paragraph future-you would want to read before the next incident in this area.

## Update log

All non-trivial updates related to this incident should be tracked directly in the doc.

- <YYYY-MM-DD> — <update summary>
