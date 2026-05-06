"""HTTP Basic Auth. v0 stub — bcrypt-hashed passwords stored on the users row."""
from __future__ import annotations

from flask import Request

from app.auth import User


def authenticate_basic(request: Request) -> User | None:
    # TODO: parse Authorization: Basic header, look up by email,
    # verify with passlib.bcrypt, return User.
    raise NotImplementedError
