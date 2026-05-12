"""Extract the ``linked_repos`` list from a markdown doc's YAML frontmatter.

Returns ``[]`` on any of:
- no frontmatter block,
- frontmatter present but no ``linked_repos`` key,
- malformed YAML,
- value isn't a list.

Non-string items are silently filtered.

Repo URLs are project metadata and shared across users; per-user
checkout paths live in ``page_working_dirs`` (Postgres-side, NOT in
git). See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.
"""

from __future__ import annotations

import re

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)


def parse_linked_repos(body: str) -> list[str]:
    match = _FRONTMATTER_RE.match(body)
    if match is None:
        return []
    try:
        data: object = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    raw: object = data.get("linked_repos")  # type: ignore[union-attr]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]  # type: ignore[reportUnknownVariableType]
