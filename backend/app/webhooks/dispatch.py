"""Build-and-send helpers for webhook destinations that aren't a trigger fire.

Today: the "send test event" that the Connectors card fires so a receiver can
learn the payload shape before a real trigger points at it. The sample carries
the same field set as a real fire with placeholder values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from app.models.webhook import WebhookEvent
from app.webhooks import client


def _str_map(obj: object) -> dict[str, str]:
    if not isinstance(obj, dict):
        return {}
    return {str(k): str(v) for k, v in cast("dict[object, object]", obj).items()}


def send_test(*, config: dict[str, Any], secret: str | None, actor: str | None) -> None:
    """POST a sample event to the config's webhook URL. Raises
    ``client.WebhookError`` / ``UnsafeUrlError`` on an unsafe URL or a failed
    send, so the caller can surface it."""
    target = cast("dict[str, Any]", config.get("config") or {})
    url = str(target.get("url") or "")
    event = WebhookEvent(
        event="trigger.test",
        trigger_id="sample",
        trigger_kind="delta",
        doc_path="Example/Sample Page.md",
        sha="0000000000000000000000000000000000000000",
        change_kind="edit",
        fired_at=datetime.now(timezone.utc).isoformat(),
        actor=actor,
        summary="This is a sample event from Agent Wiki.",
        reason="test event from the Connectors settings page",
        routing_tag=target.get("routing_tag"),
        fields=_str_map(target.get("fields")),
    )
    client.deliver(
        url=url,
        body=event.model_dump_json().encode(),
        headers=_str_map(target.get("headers")) or None,
        secret=secret,
    )
