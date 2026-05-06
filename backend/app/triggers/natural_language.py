"""LLM-backed evaluator: 'does this doc delta satisfy this NL trigger?'."""
from __future__ import annotations


def matches(nl_description: str, before_body: str, after_body: str) -> tuple[bool, str]:
    # TODO: small focused LLM call. Keep prompt cheap — this fires per doc
    # update per matching trigger.
    raise NotImplementedError
