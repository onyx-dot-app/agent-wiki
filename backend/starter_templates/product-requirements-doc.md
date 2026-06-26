---
name: Product Requirements Doc
description: Engineering proposal (PRD) — context, problem, proposal, alternatives, rollout, and risks.
update_instruction: >-
  Record each change as a dated entry in the Change log section; don't rewrite
  prior entries.
---
# PRD: <title>

**Author(s):** <name>
**Status:** Draft | In review | Accepted | Rejected | Superseded
**Created:** <YYYY-MM-DD>
**Reviewers:** <names>
**Stakeholders:** <names or teams>

## Summary

What is the high level problem (or opportunity). Why is it important to our users/business? What insights / assumptions are we making?

## Background and context

What does someone need to know to evaluate this proposal? Existing system, recent history, prior attempts, links to relevant other wiki docs, tickets, or PRs.

## Problem

What is broken, missing, or limiting today? Be concrete — symptoms, frequency, who is affected, what it costs us. Avoid jumping straight to a solution here.

## Goals

- <goal 1>
- <goal 2>

## Non-goals

Explicitly out of scope. Anchoring this up front prevents scope creep in review.

- <non-goal 1>

## Proposal

The proposed approach in detail. Use sub-sections as needed.

### Overview

A few sentences a reviewer can read and immediately picture the shape of the change.

### Detailed design

APIs, data models, interfaces, code-level shape. Include diagrams or pseudocode where they help.

### Data model changes

New tables, new columns, indexes, migration shape. Note any backfill requirements.

### Migration and backfill

How existing data and existing callers move to the new shape. What runs online, what runs as a one-shot.

### Rollout plan

How this ships safely — feature flags, staged rollout, kill switches, dark-launch, what to watch during ramp.

### Observability

Logs, metrics, dashboards, and alerts that change as a result of this work. What does "healthy" look like after the change?

### Security and privacy

Auth surface, data classification, PII implications, threat-model deltas. Note anything that needs a security review before ship.

## Alternatives considered

For each: what it would look like, and why we are not picking it.

### Alternative A: <name>

### Alternative B: <name>

### Do nothing

Always include this. What is the cost of leaving the status quo in place?

## Open questions

Questions the author wants reviewers to weigh in on. Move resolved ones into the body.

- <question awaiting an answer>

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
|  |  |  |  |

## Success metrics

How will we know this worked? What do we expect to see in <metric> over <timeframe>? Define both leading and lagging indicators where it is useful.

## Timeline and phasing

Rough milestones and owners. Not a Gantt chart — a sketch of sequencing.

## Appendix

Reference links, supporting analysis, prototype notes, raw benchmark numbers.

## Change log

- <YYYY-MM-DD> — <update summary>
