---
name: Runbook
description: Operational runbook — symptom, diagnosis, fix, and escalation path.
---
# Runbook: <symptom or alert name>

**Owner:** <team or oncall rotation>
**Last verified:** <YYYY-MM-DD>
**Related dashboards:** <links>
**Related alerts:** <alert names or links>

## What this covers

One or two sentences. The symptom or alert that should bring an oncall here.

## Severity guidance

When to escalate, when to wait, and when this is safe to investigate during business hours.

## Quick checks

The five-minute triage — fast commands and dashboards that narrow the problem.

1.
2.
3.

## Common causes and fixes

### Cause A: <short name>

**Signal:** <how you confirm this is what is happening>

**Fix:**

```bash
<command>
```

**Verify:** <what you should see after the fix>

### Cause B: <short name>

**Signal:**

**Fix:**

**Verify:**

## If none of the above

Deeper investigation steps — where to look in logs, which queries to run, who to page if the symptom is unfamiliar.

## Escalation

- **Primary oncall:** <how to page>
- **Backup:** <how to page>
- **Subject-matter experts:** <names or handles, and what they own>

## Known gotchas

The "do not do this" notes that have bitten people before. Worth more than any other section here, because most readers will skim straight to fixes.

-

## Update log of playbook

All updates to the playbook should be tracked along with the time that the update was made, add it to the list below:

- <YYYY-MM-DD> — <update summary>

## Recent incidents that hit this runbook

- <YYYY-MM-DD> — <link to postmortem>
