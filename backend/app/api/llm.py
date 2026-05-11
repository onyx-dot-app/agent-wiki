"""FastAPI port of ``app/api/llm.py`` (Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth.deps import require_user
from app.llm import providers as llm_providers
from app.llm import settings as llm_settings
from app.llm.errors import LLMError
from app.models.llm import LLMStatusResponse

router = APIRouter()


@router.get("/status", response_model=LLMStatusResponse)
def status(_user: User = Depends(require_user)) -> LLMStatusResponse:
    s = llm_settings.get()
    provider = llm_providers.get(s.provider) if s.provider else None
    configured = False
    if provider is not None and s.model:
        try:
            provider.check_configured(s)
            configured = True
        except LLMError:
            configured = False
    return LLMStatusResponse(configured=configured, provider=s.provider)
