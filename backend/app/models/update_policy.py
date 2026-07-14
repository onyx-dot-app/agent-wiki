"""HTTP shapes for /api/update-policy."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.wiki import PageKind


class EffectivePolicy(BaseModel):
    """The resolved policy for a path after the most-granular-wins cascade."""

    ingestion_auto_update_disabled: bool
    update_instruction: str | None = None
    ai_management_allowed: bool = False


class ExplicitPolicy(BaseModel):
    """The policy row set on exactly this path (no cascade)."""

    path: str
    kind: PageKind
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None
    ai_management_allowed: bool | None = None
    warn_update_threshold: int | None = None
    updated_by_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UpdatePolicyResponse(BaseModel):
    explicit: ExplicitPolicy | None = None
    effective: EffectivePolicy


class UpdateHealthResponse(BaseModel):
    """Auto-update health facts for a page over a sliding trailing-24h window —
    the raw numbers; the client decides what to surface (slider value/max,
    too-frequent-update banner)."""

    path: str
    count_24h: int  # ingestion auto-updates in the last 24h
    threshold_24h: int  # per-page warning threshold, updates/24h (0 = every update)
    cap_24h: int  # admin global cap, updates/24h (slider max; 0 = no cap)
    auto_update_disabled: bool  # effective: ingestion auto-update is off here
    can_manage: bool  # viewer has write access — may act on the warning
    # When over the cap, ISO-8601/UTC time the page drops back under it and
    # auto-update resumes; None when not over the cap.
    cap_resets_at: str | None = None


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
    ai_management_allowed: bool | None = None
    warn_update_threshold: int | None = Field(default=None, ge=0)
