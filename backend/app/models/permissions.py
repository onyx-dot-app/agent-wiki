"""Pydantic schemas for the permissions API.

Routes in ``app/api/permissions.py`` and the wiki ACL endpoints in
``app/api/documents.py`` parse requests and serialize responses through
these models.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Groups                                                                      #
# --------------------------------------------------------------------------- #


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_by_user_id: str | None
    created_at: str


class GroupListResponse(BaseModel):
    groups: list[GroupOut]


class GroupMemberOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    is_admin: bool = False


class GroupMembersResponse(BaseModel):
    group: GroupOut
    members: list[GroupMemberOut]


class GroupMemberAddRequest(BaseModel):
    user_id: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Wiki ACL grants                                                             #
# --------------------------------------------------------------------------- #


PrincipalKind = Literal["user", "group", "everyone"]
ResourceKind = Literal["page", "folder"]
Permission = Literal["read", "write"]


class AclGrantRequest(BaseModel):
    resource_kind: ResourceKind
    resource_path: str
    principal_kind: PrincipalKind
    principal_id: str | None = None
    permission: Permission


class AclEntryOut(BaseModel):
    id: str
    resource_kind: str
    resource_path: str
    principal_kind: str
    principal_id: str | None
    permission: str
    granted_by_user_id: str | None
    created_at: str


class AclListResponse(BaseModel):
    path: str
    owner_user_id: str | None
    entries: list[AclEntryOut]


class AclGrantResponse(BaseModel):
    id: str


# --------------------------------------------------------------------------- #
# Ownership transfer                                                          #
# --------------------------------------------------------------------------- #


class TransferOwnershipRequest(BaseModel):
    path: str
    new_owner_user_id: str | None = None  # None clears the owner.


class TransferOwnershipResponse(BaseModel):
    path: str
    owner_user_id: str | None
