You are consolidating an ENTITY TYPE TAXONOMY that was derived bottom-up. Because each type was named by a separate call that saw only its own members, the list is over-split: the same KIND of thing appears under several names, and rare kinds got their own hyper-specific type when a broader one already covers them.

You see every type at once, with how many referents and pages each covers.

TASK: merge types that denote the same KIND of thing.
  - Merge a narrow type into a broader one when the broader one already covers its members: "medical_organization", "telecommunications_company" and "nonprofit_organization" are all "organization".
  - Merge synonyms: types whose definitions are interchangeable are one type, even when the names differ ("software_product" / "software_service" / "software_application").
  - PREFER FEW, GENERAL types. A taxonomy of 5-10 types that a later extractor can apply consistently beats 40 precise ones it cannot choose between. Referent counts are your guide: a type with 1 referent almost always belongs inside a larger one.
  - Do NOT merge kinds that a later extractor would need to tell apart — a person is not an organization, a protocol is not a product.
  - Reject a type whose basis is not the KIND of thing:
      * FORM-based: "abbreviation" classifies how a name is SPELLED, not what it denotes. Every acronym would qualify, so it steals members from every real type.
      * NEGATIVE: "non_organization_reference" defines by what a thing is NOT. That is a junk drawer, not a category, and it will absorb anything unclear.
      * FUNCTIONAL co-occurrence: do not group by what a thing is USED WITH. Okta is a COMPANY even though it is discussed alongside OAuth and SAML; a vendor of authentication software is not an authentication protocol.
  - Judge a type by its MEMBERS, not by its name. A plausible label can sit on top of members that belong elsewhere: if the referents under "place" are all deployment regions, merge that whole type into infrastructure; if the referents under "website" are all companies' domains, merge it into organization. You reassign WHOLE types, not individual referents, so decide from the examples which kind the type as a whole is — and where a type is genuinely mixed, leave it rather than moving it somewhere only some of its members fit.
    Fold such members into the type their referent actually belongs to. Every type you output must be statable as "a kind of X" where X is a thing, not a property of its name and not the absence of something.

For each merged type give a canonical type_name (lower_snake_case) and ONE definition that covers all its members and is decidable by a later extractor reading it alone.

Every input index MUST appear in exactly one output type.

OUTPUT: a single JSON object, no prose:
{"types": [{"type_name": "organization", "definition": "A named company, customer, vendor, institution, or other organisation.", "member_indices": [1, 4, 9]}]}
