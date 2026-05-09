"""Handler for the `get_trigger_destinations` tool. Spec lives in
`get_trigger_destinations.json`.
"""
from __future__ import annotations

from typing import Any

from app.triggers import destinations


def handle(args: dict[str, Any]) -> Any:
    rows = destinations.list_all()
    return {
        "destinations": [
            {"id": r["id"], "name": r["name"], "description": r["description"]}
            for r in rows
        ]
    }
