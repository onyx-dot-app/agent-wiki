"""Handler for the `list_templates` tool. Spec lives in `list_templates.json`.

Lists the admin-managed document templates so an agent can pick the right
starting point for a new page and see the update policy it would inherit.
Read-only; templates are global content (any authenticated principal may
list them, same as the `/api/templates` picker endpoint).
"""
from __future__ import annotations

from typing import Any

from app.wiki import templates as templates_repo


def handle(args: dict[str, Any]) -> Any:
    templates = [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "body": t["body"],
            "auto_update_disabled": t["ingestion_auto_update_disabled"],
            "update_instruction": t["update_instruction"],
        }
        for t in templates_repo.list_all()
    ]
    return {"templates": templates}
