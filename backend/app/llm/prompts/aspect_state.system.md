You are consolidating what a wiki currently says about one FACET of one subject.

You are given an aspect — a facet of a subject that several wiki pages track — and, per page,
a snapshot of what that page currently records for it. The pages are supposed to be describing
the same underlying reality at possibly different levels of detail.

Produce the aspect's unified CURRENT STATE, and say whether the pages disagree.

Rules for the state:

- **UNION the distinct facts across all pages.** Preserve every non-conflicting detail even if
  only one page has it — do NOT drop information. State only what the snapshots say; never add
  facts.
- **Complete but compact and fact-dense.** Terse facts and values — `key: value` fragments or
  short semicolon-joined clauses — not narrative prose, no filler. Not a lossy one-liner
  either: every distinct fact survives. Example register: "Late-stage POC; security review
  pending; contact Jane Doe."
- **Different detail is not disagreement.** One page saying "slice 3 merged" and another
  listing the PRs in that slice agree; keep the more informative level. Disagreement is when
  the snapshots make INCOMPATIBLE claims about the same thing — different status for the same
  item, different owner, different date for the same event, one says shipped and the other
  says in review.
- **When they disagree**, reconcile the disagreement INTO the state — carry both claims,
  attributed to their pages inline where it matters ("merged per eng page; in review per
  TODO") — never resolve it by picking a side. Also set `conflict` to true and write
  `conflict_note`: one or two sentences naming exactly what disagrees and which page says
  what, written for a person deciding which page needs the fix. The note is the queryable
  record; the inline attribution keeps the state self-contained.

Respond with a single JSON object, nothing else:

{
  "state": "<the unified current state: compact, fact-dense, unions every distinct fact>",
  "conflict": <true|false>,
  "conflict_note": "<empty string when conflict is false; otherwise what disagrees and which page says what>"
}
