"""Email whitelist for signup.

If ``ALLOWED_EMAILS`` is empty (or unset), signup is **open** — anyone can
create an account. If it's a comma-separated list, only those exact addresses
can sign up. Wildcards (e.g. ``*@onyx.app``) are supported on the domain part.
"""
from __future__ import annotations

import os


def _entries() -> list[str]:
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def is_open() -> bool:
    return not _entries()


def is_allowed(email: str) -> bool:
    email = email.strip().lower()
    entries = _entries()
    if not entries:
        return True
    for entry in entries:
        if entry == email:
            return True
        if entry.startswith("*@") and email.endswith(entry[1:]):
            return True
    return False
