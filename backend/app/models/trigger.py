from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TriggerAction(BaseModel):
    kind: Literal["webhook", "http", "agent_message"]
    config: dict


class Trigger(BaseModel):
    id: str
    owner_user_id: str
    scope_path: str
    kind: Literal["delta", "schedule"]
    nl_description: str
    action: TriggerAction
    schedule_cron: str | None = None
    enabled: bool = True
    created_at: str
