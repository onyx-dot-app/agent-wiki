# Exploration work

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns the open-ended R&D the V0 brief
> explicitly calls out as "exploration." These items don't have a clean
> spec yet; the goal is to learn enough to write one. **None of these are
> blocking V0 ship-readiness.**

_Last updated: 2026-05-06_

---

## 1. Coding-agent doc-update discipline

### The question (verbatim from V0 brief)
> "How to get agents (coding agents for now) to reliably update the docs
> instead of too often or never. Is this just an MCP description? Should
> it be a skill?"

### Why it matters
The pitch — "wikis that stay current as work happens" — relies on coding
agents (Claude Code, Cursor, etc.) opportunistically updating high-level
project plans + status pages. Two failure modes:

- **Too often** — every commit produces a noisy doc edit; humans tune out.
- **Never** — agents avoid touching docs because the contract is fuzzy.

### Hypotheses to test

| Approach | What it looks like | Pros | Cons |
|---|---|---|---|
| MCP tool description only | Agent-wiki exposes an MCP server (`update_doc`, `read_doc`, `list_triggers`); the description tells agents when to use it | Lowest friction for any agent; standard MCP UX | Tool descriptions are easy to ignore; no agent-side rules engine |
| Skill / agent-instruction file | Per-org or per-repo skill that says "before opening a PR, check for project plans matching the changed area and update if scope shifts" | Concrete trigger conditions; reusable across agents | Requires per-environment install; risks bit-rot |
| Webhook-driven (no agent action) | Agent does nothing; Onyx pushes the PR/commit info via the ingest API and our doc-updater agent does the editing | No coding-agent change at all; centralizes the editing logic | Doesn't capture intent — only outcome — so the doc lags one step |
| Hybrid | Coding agent emits a structured "context update" (plan changes, decisions made) into a known doc convention; our doc-updater enriches | Captures both intent and outcome | Most moving parts |

### Concrete experiments to run (cheap, parallelizable)
1. Stand up the MCP server — `update_doc(path, body, message)` and
   `search_wiki(query)`. Connect Claude Code locally. See whether it
   uses them unprompted on PR work in our own repo.
2. Write a candidate skill (markdown file with step-by-step "when to
   touch the wiki"). Try it on a small repo; measure unprompted-update
   rate.
3. Wire ingest from Onyx GitHub PRs (Onyx-push work) and see how good
   the doc-updater agent's edits are without coding-agent involvement.

Output: a short writeup recommending one approach (or a hybrid). Add
back to `architecture_and_progress.md` as a decision row.

### Open sub-questions
- Is the right unit of update the **wiki page** or a **change log + agent
  reconciliation**? V0 architecture assumes the latter (agent rewrites
  the page from a stream of events).
- How do we evaluate quality? "Did a human edit it later?" is a useful
  signal we already have — every commit by a non-`agent-wiki@local`
  identity is a correction.

---

## 2. Onyx-side push for public connectors

Mostly a contract + integration effort, tracked separately under
`onyx-push/onyx-push.md`. The exploration angle that lives here:

- **What does "all document changes" actually mean?** Many connectors
  produce high-volume noise (every Slack message, every Drive autosave).
  We probably want connector-specific filters baked in. Defining that
  filter set is exploration.
- **How chunky is the payload?** A Drive doc edit is potentially huge.
  Likely we ship the change kind + a snippet, not the full body.
- **Routing.** Does Onyx know which wiki doc(s) to target, or do we run
  an agent to choose? V0: Onyx picks via configured mappings; the
  exploration is whether that's enough or whether we need an inference
  step on our side.

---

## Progress

Nothing has been built or formally explored yet. This file is a parking
lot. Once an experiment lands, append a "Findings" subsection under the
relevant question and link to any code in the main repos.

### Next up
- Pick **one** experiment from §1 to run first. Recommendation: stand up
  the MCP server (a few hours of work given the existing API) and pair
  with Claude Code on actual feature work for a week. Concrete signal
  beats more speculation.
- Once Onyx-push is even loosely wired, capture a week of payloads and
  use them to inform the connector-filter list in §2.

### Out of scope (do not pull forward)
- Anything in this doc is **explicitly not on the V0 critical path**. If
  you find yourself building something here as part of "V0 ship-readiness,"
  stop and re-read the V0 brief in `architecture_and_progress.md` §2.
