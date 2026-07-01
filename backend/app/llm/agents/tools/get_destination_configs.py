"""Handler for the `get_destination_configs` tool. Spec lives in
`get_destination_configs.json`.
"""
from __future__ import annotations

from typing import Any

from app.auth import current_user
from app.triggers import destination_configs


def handle(args: dict[str, Any]) -> Any:
    user = current_user()
    if user is None:
        return {"error": "no authenticated user"}
    rows = destination_configs.list_for_user(user.id)
    return {
        "destination_configs": [
            {"id": r["id"], "type": r["type"], "name": r["name"]} for r in rows
        ]
    }
