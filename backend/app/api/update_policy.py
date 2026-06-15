"""FastAPI router for /api/update-policy — per-page / per-folder update policy.

Thin HTTP layer over ``app/wiki/update_policy.py``. Reads/writes are gated by
``require_can`` on the target path, the same permission as editing it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import User, require_can
from app.auth.deps import require_user
from app.models.update_policy import (
    EffectivePolicy,
    ExplicitPolicy,
    SetUpdatePolicyRequest,
    UpdatePolicyResponse,
)
from app.wiki import update_policy as policy_repo

router = APIRouter()
log = logging.getLogger(__name__)


def _normalize(path: str) -> str:
    try:
        return policy_repo.normalize_path(path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _build_response(path: str) -> UpdatePolicyResponse:
    explicit = policy_repo.get(path)
    effective = policy_repo.resolve_for_path(path)
    return UpdatePolicyResponse(
        explicit=ExplicitPolicy(**explicit) if explicit is not None else None,
        effective=EffectivePolicy(
            ingestion_auto_update_disabled=effective.ingestion_auto_update_disabled,
            update_instruction=effective.update_instruction,
        ),
    )


@router.get("/update-policy")
def get_update_policy(
    path: str, user: User = Depends(require_user)
) -> UpdatePolicyResponse:
    norm = _normalize(path)
    require_can("read", norm, user)
    return _build_response(norm)


@router.put("/update-policy")
def put_update_policy(
    req: SetUpdatePolicyRequest, user: User = Depends(require_user)
) -> UpdatePolicyResponse:
    norm = _normalize(req.path)
    require_can("write", norm, user)
    policy_repo.set_policy(
        norm,
        ingestion_auto_update_disabled=req.ingestion_auto_update_disabled,
        update_instruction=req.update_instruction,
        actor_user_id=user.id,
    )
    return _build_response(norm)


@router.delete("/update-policy")
def delete_update_policy(
    path: str, user: User = Depends(require_user)
) -> UpdatePolicyResponse:
    norm = _normalize(path)
    require_can("write", norm, user)
    policy_repo.delete(norm)
    return _build_response(norm)
