"""HTTP shapes for /api/user/settings.

The ``UserSettings`` model is the *only* schema for the JSONB blob in
``users.settings`` — adding a new preference means:

  1. Add a new field here with a sensible default.
  2. Read it on the frontend via the auth context (``user.settings.<field>``).

No DB migration is needed (the column is JSONB). Existing rows whose
JSON predates the new field load fine because the model fills the
default during validation.
"""
from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserSettings(BaseModel):
    """Per-user preferences. All fields have defaults so an empty
    ``{}`` JSONB row produces a fully-populated settings object."""

    model_config = ConfigDict(extra="ignore")

    theme: Literal["light", "dark", "system"] = "system"
    timezone: str = "UTC"
    default_landing: Literal["wiki_home", "recent", "last_viewed"] = "wiki_home"
    # Preferred provider + model for chat. None = use the global agent settings.
    chat_provider: str | None = None
    chat_model: str | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


class UserSettingsUpdate(BaseModel):
    """Partial settings update — every field optional, merged into the
    existing settings before validation."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["light", "dark", "system"] | None = Field(default=None)
    timezone: str | None = Field(default=None)
    default_landing: Literal["wiki_home", "recent", "last_viewed"] | None = Field(default=None)
    chat_provider: str | None = Field(default=None)
    chat_model: str | None = Field(default=None)

    def non_null(self) -> dict[str, Any]:
        sent = self.model_fields_set
        result: dict[str, Any] = {}
        for k, v in self.model_dump().items():
            if k not in sent:
                continue
            result[k] = v
        return result
