You are given ONE wiki page (its path, then its content). List the real-world REFERENTS it mentions — the specific, nameable things the page refers to or tracks.

A referent is something with a NAME that could be looked up or pointed at: a company, a person, a team, a product, a tool, a system, a place, a named programme or initiative. Include a referent the page merely mentions, not only ones it is about.

Do NOT list:
  - generic nouns or activities ("deployment", "a meeting", "the backlog")
  - section headings, page titles, or document structure
  - dates, numbers, URLs, file paths, code identifiers, ticket ids
  - descriptive phrases; give the NAME only ("Acme", not "the Acme POC deal")

SPLIT COMPOUND REFERENTS into separate entries. "Softchoice / QuikTrip" is TWO referents, not one; so is "Acme and its Widget product" (an organization and a product). A slash, an ampersand, "and", or a parenthesised alias each usually signals two things — unless the punctuation is part of a single registered name ("Rohde & Schwarz", "AT&T").

Give each referent exactly as the page writes it (do not normalise spelling or expand abbreviations — variants are folded later), plus a SHORT phrase saying what it is, in your own words. Do not assign it to a category or type; describe it plainly. The categories are what we are deriving, so naming one here would presuppose the answer.

List every distinct referent once, however many times it appears.

OUTPUT: a single JSON object, no prose: {"referents": [{"name": "...", "what": "..."}]}. Use an empty list if the page names none.
