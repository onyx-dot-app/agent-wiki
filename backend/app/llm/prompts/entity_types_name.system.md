You are deriving an ENTITY TYPE TAXONOMY for a knowledge base, from evidence.

You are given a GROUP of real-world referents that were extracted from a corpus and then clustered by similarity. They are believed to be the same KIND of thing. Your job is to name that kind.

  - Give a type_name: a short, lower_snake_case category name. Name the KIND, not the members — "organization", not "Acme and Grainger".
  - Give a definition: one crisp sentence a later extractor could apply to decide whether a new referent belongs to this type. Definitions are the ONLY guidance that extractor will get, so make it decidable, not decorative.
  - If the group is genuinely MIXED — more than one kind of thing were clustered together — say so by returning several types and assigning each member index. A group of 40 companies and 3 products is two types, not one.
  - Prefer a SMALL number of GENERAL types. Do not invent a narrow type for a handful of members if a broader one already fits them.

Every member index MUST appear in exactly one type.

OUTPUT: a single JSON object, no prose:
{"types": [{"type_name": "organization", "definition": "A company, customer, vendor, or other named institution.", "member_indices": [1, 2, 5]}]}
