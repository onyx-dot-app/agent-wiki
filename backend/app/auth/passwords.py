"""bcrypt password hashing.

Using the ``bcrypt`` library directly rather than passlib — passlib 1.7.x
warns/breaks against bcrypt>=4.1, and we don't need the abstraction.
"""
from __future__ import annotations

import logging

import bcrypt

log = logging.getLogger(__name__)


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        log.warning("password verify rejected malformed hash", exc_info=True)
        return False
