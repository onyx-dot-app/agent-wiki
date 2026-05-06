from __future__ import annotations

from pydantic import BaseModel


class Event(BaseModel):
    id: int
    ts: str
    kind: str          # doc.update | trigger.fire | webhook.in | ...
    actor: str | None
    target: str | None
    payload: dict
