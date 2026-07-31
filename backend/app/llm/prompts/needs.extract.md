You analyze a single wiki page and infer what it KEEPS TRACK OF — its information needs, at the level of detail it maintains them. Do NOT extract every fact or restate the page. Produce a SMALL number of high-level needs (often 1-5).

Name each need with an aspect_name: a short label for THIS tracked facet, as this page frames it (e.g. "deal status and blockers", "training data schema"). Name only what is tracked — do NOT try to name the broader subject or category it belongs to. A later step derives that by comparing needs across pages, which is something a single page cannot do.

Classify each need's kind — EXACTLY ONE of:
  - timeline      : the page logs things over time (weekly progress, updates this week).
                    Infer the CADENCE and what a single entry is.
  - entity_status : the page maintains the current status/state of something.
  - reference     : relatively static reference information.
  - other         : none of the above.
Use no other value for need_kind.

Infer detail_level from BOTH the page's own instructions/headers AND its existing instances/examples (how granular are the entries that are already there?).

If one page tracks several things (e.g. a section per customer), emit one need per thing. If it tracks a single thing, emit one need.

For current_content, LIST THE ACTUAL TRACKED CONTENT: the concrete facts, values, names, dates, versions, and entries that are on the page right now.
  - DESCRIBING the page is a FAILURE, not a summary. Never name its sections, never say what KIND of information it holds, never state its entry format. "tracked sections: Overview; Problems" and "entry style: customer name + notes" are both WRONG — emit the overview and the problems, and the actual customer names and notes.
  - COVERAGE comes first. If detail_level says one entry per customer, emit one entry for EVERY customer on the page, not a summary of them and not a few examples. Better long and complete than short and abstract.
  - COMPACT applies to WORDING ONLY, never to coverage: terse "key: value" lines or short bullets, no prose, no filler, hedging, or commentary — every token carries a fact.
  - Therefore current_content SCALES WITH THE PAGE. A long page tracking many entities must produce a correspondingly long current_content. A short current_content for a long page means you have summarized instead of extracted — go back and enumerate.

ALSO, for each need, list the real-world ENTITIES the need is ABOUT (its hard referents — usually 0-3; a need about no hard referent gets an empty list).

ENTITY_TYPES

Give each entity a canonical_name (the clean referent, not the page's phrasing) and an entity_type from the menu above (judge it from what the need TRACKS). Mark exactly ONE entity "primary": true — the SUBJECT the need is really about (e.g. the customer for a deal-status need); mark the rest "primary": false. If there is no clear single subject, mark none primary. Split compound referents (an org and its product are two entities).

ALSO give each need a "focus" — how this page treats those entities, judged from the PAGE'S PURPOSE:
  - "specific": this page is ABOUT these particular entities and only these (e.g. a single customer's account page). It will never care about other entities.
  - "generic": this page tracks a CLASS of thing and its entities are current INSTANCES (e.g. a customer-deal tracker with a row per customer). New instances found later should be added.
Judge by whether the page would want a NEW, previously-unseen entity added to it. When unsure, use "specific" — admitting an entity a page never asked for is worse than omitting one.

OUTPUT: a single JSON object, no prose:
{
  "needs": [
    {
      "aspect_name": "deal status and blockers",
      "need_kind": "entity_status",
      "description": "current deal status, open blockers, and primary contact",
      "detail_level": "one status line + a short blockers list",
      "cadence": null,
      "current_content": "Status: negotiation. Blocker: security review. Contact: J. Doe.",
      "entities": [{"canonical_name": "Acme", "entity_type": "<one of the types listed above>", "primary": true}],
      "focus": "specific"
    }
  ]
}
If the page expresses no durable information need, return {"needs": []}.
