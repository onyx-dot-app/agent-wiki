You are consolidating what a wiki currently says about one FACET of one subject.

You are given an aspect — a facet of a subject that several wiki pages track — and, per page,
a snapshot of what that page currently records for it. The pages are supposed to be describing
the same underlying reality at possibly different levels of detail.

Produce the aspect's unified CURRENT STATE, and say whether the pages disagree.

Rules:

- **State only what the snapshots say.** The unified state is a faithful merge of the inputs,
  not your judgment of what is probably true. Never add facts, never resolve a disagreement by
  picking a side.
- **Different detail is not disagreement.** One page saying "slice 3 merged" and another
  listing the PRs in that slice agree; unify them at the more informative level. Disagreement
  is when the snapshots make INCOMPATIBLE claims about the same thing — different status for
  the same item, different owner, different date for the same event, one says shipped and the
  other says in review.
- **When they disagree**, still produce the best unified state you can (carry both claims,
  attributed to their pages), set `conflict` to true, and write `conflict_note`: one or two
  sentences naming exactly what disagrees and which page says what — written for a person
  deciding which page needs the fix.
- **Stay in the inputs' register**: plain prose, compact, present tense. No headers, no
  markdown structure. A reader should get the facet's current answer in one breath.

Respond with a single JSON object, nothing else:

{
  "state": "<the unified current state, one short paragraph>",
  "conflict": <true|false>,
  "conflict_note": "<empty string when conflict is false; otherwise what disagrees and which page says what>"
}
