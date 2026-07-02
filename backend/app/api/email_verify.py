"""Public landing endpoint for email verification links.

Deliberately unauthenticated: the link may be opened in an inbox that is not
the config owner's, and the click is the consent. The single-use expiring
token is the capability."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.triggers import email_verification

log = logging.getLogger(__name__)

router = APIRouter()

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Agent Wiki</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 480px; margin: 15vh auto; text-align: center;">
<h2>{heading}</h2><p>{body}</p>
</body></html>"""


@router.get("/verify", response_class=HTMLResponse)
def verify_email(token: str = "") -> HTMLResponse:
    config_id = email_verification.verify(token) if token else None
    if config_id is None:
        return HTMLResponse(
            _PAGE.format(
                heading="Link invalid or expired",
                body="This verification link was already used, has expired, "
                "or is not valid. Ask for a new one from the wiki's "
                "notification settings.",
            ),
            status_code=400,
        )
    return HTMLResponse(
        _PAGE.format(
            heading="Address verified",
            body="This address can now receive Agent Wiki notifications. "
            "You can close this tab.",
        )
    )
