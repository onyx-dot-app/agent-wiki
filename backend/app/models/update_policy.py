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
    warn_update_threshold: int | None = None
    updated_by_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UpdatePolicyResponse(BaseModel):
    explicit: ExplicitPolicy | None = None
    effective: EffectivePolicy


class PatchUpdatePolicyRequest(BaseModel):
    """Partial update for a path (PATCH semantics).

    Only fields present in the request body are changed; omitted fields are left
    as-is, so setting one field never disturbs the other's inherited state. A
    field sent as ``null`` clears it (back to inherit); when the row ends up with
    no setting it is removed. The router keys off ``model_fields_set`` to tell
    "omitted" from an explicit ``null``.
    """

    path: str
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None
    warn_update_threshold: int | None = None
