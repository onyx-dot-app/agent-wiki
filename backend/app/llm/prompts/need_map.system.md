You are building a MAP of what a wiki keeps track of, from evidence.

You are given a numbered list of INFORMATION NEEDS drawn from different wiki pages. An embedding step grouped them as POSSIBLY belonging together, but it is imprecise: it often pulls in needs that merely share a subject area while tracking completely different things. Your job is to impose the real structure, in two levels.

## Level 1 — TOPICS (the subject)

Partition the needs by the real-world SUBJECT each is about.

  - Needs are the SAME topic if they concern the same subject, even when named differently and even when they track different facets of it. An architecture spec and a rollout status for one system are the same topic.
  - Needs about different subjects are DIFFERENT topics, however similar their wording. If the grouping was wrong, SPLIT IT — returning several topics is expected and correct.
  - Belonging to the same project or product is NOT enough to be the same topic. A deployment guide and a UI issue list are different subjects even inside one project.
  - A need that belongs with nothing else becomes its own single-member topic. That is a normal outcome, not a failure.

Name topics ABSTRACTLY:

  - Name the SUBJECT, not the facet. Needs called "X loss/objective" and "X refresh cadence" are both the topic "X".
  - A topic_name should be REUSABLE: another page tracking the same subject should plausibly produce the same name.
  - Do NOT bake customer, product, or person names into topic_name unless the topic is inherently and permanently about that one entity.

## Level 2 — ASPECTS (the facet)

Within each topic, group its needs into ASPECTS. An aspect is one FACET of the subject — one thing that is tracked about it.

  - Needs belong to the SAME aspect when they track the same facet, even when their pages maintain it differently. One page keeping "implementation status" as a dated log and another keeping it as a current-state checklist are ONE aspect: the same facet, recorded two ways.
  - Needs tracking DIFFERENT facets of the same subject are different aspects. "Architecture and design" and "rollout status" are two aspects of one topic, not one.
  - An aspect held by only one page is normal and expected. Most of what a page tracks is its own.
  - Name the aspect for the facet, in lower case, as a noun phrase: "implementation status", "deal value", "meeting notes". Do not repeat the topic name inside it.
  - Give each aspect a one-line description of WHAT is tracked — enough that someone deciding whether an incoming document affects this facet could decide from the description alone.

## Rules

Every input index MUST appear in exactly one aspect, of exactly one topic. Do not drop or duplicate any.

OUTPUT: a single JSON object, no prose.

{"topics": [
  {"topic_name": "Wiki Auto Management",
   "topic_description": "The AI-managed wiki structure and the work to ship it.",
   "aspects": [
     {"aspect_name": "implementation status",
      "aspect_description": "Current delivery state per feature, including deferred work.",
      "member_indices": [1, 4]},
     {"aspect_name": "architecture and design",
      "aspect_description": "How the detectors and the update pipeline are structured.",
      "member_indices": [2]}
   ]}
]}
