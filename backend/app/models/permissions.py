"""Pydantic schemas for the permissions API.

Routes in ``app/api/permissions.py`` and the wiki ACL endpoints in
``app/api/wiki.py`` parse requests and serialize responses through
these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.wiki import PageKind


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
    # Aggregate counts for the groups list UI. Default 0 so callers that
    # build a GroupOut straight from the repo dict (group detail, create)
    # don't have to supply them.
    member_count: int = 0
    page_count: int = 0
    folder_count: int = 0


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


class GroupUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GroupShareOut(BaseModel):
    """A page or folder shared with a group — one ACL row, group-centric."""

    id: str
    resource_kind: str
    resource_path: str
    permission: str
    created_at: str


class GroupSharesResponse(BaseModel):
    shares: list[GroupShareOut]


# --------------------------------------------------------------------------- #
# Wiki ACL grants                                                             #
# --------------------------------------------------------------------------- #


PrincipalKind = Literal["user", "group", "everyone"]
Permission = Literal["read", "write"]


class AclGrantRequest(BaseModel):
    resource_kind: PageKind
    resource_path: str
    principal_kind: PrincipalKind
    principal_id: str | None = None
    permission: Permission


class AclEntryOut(BaseModel):
    id: str
    resource_kind: PageKind
    resource_path: str
    principal_kind: str
    principal_id: str | None
    permission: str
    granted_by_user_id: str | None
    created_at: str
    # Display enrichment for the share UI (resolved server-side so the
    # client doesn't need the admin-only user list). None for principals
    # that no longer exist or for the `everyone` principal.
    principal_email: str | None = None
    principal_name: str | None = None
    group_name: str | None = None


class AclListResponse(BaseModel):
    path: str
    owner_user_id: str | None
    owner_email: str | None = None
    owner_name: str | None = None
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
