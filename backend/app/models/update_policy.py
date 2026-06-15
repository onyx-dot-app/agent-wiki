"""HTTP shapes for /api/update-policy."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EffectivePolicy(BaseModel):
    """The resolved policy for a path after the most-granular-wins cascade."""

    ingestion_auto_update_disabled: bool
    update_instruction: str | None = None


class ExplicitPolicy(BaseModel):
    """The policy row set on exactly this path (no cascade)."""

    path: str
    kind: Literal["page", "folder"]
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None
    updated_by_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UpdatePolicyResponse(BaseModel):
    explicit: ExplicitPolicy | None = None
    effective: EffectivePolicy


class SetUpdatePolicyRequest(BaseModel):
    """Full desired state for a path (PUT semantics).

    Both fields default to ``null`` = inherit/clear. When the resulting row
    carries no setting it is removed.
    """

    path: str
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None
