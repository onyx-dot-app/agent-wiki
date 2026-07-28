"""FastAPI port of ``app/api/permissions.py`` (Phase 3)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.db.models import AclEntry
from app.db.session import session
from app.auth import User
from app.auth import groups as groups_repo
from app.auth import users as users_repo
from app.auth.deps import require_admin, require_user
from app.models.permissions import (
    AclEntryOut,
    AclGrantRequest,
    AclGrantResponse,
    AclListResponse,
    GroupCreateRequest,
    GroupListResponse,
    GroupMemberAddRequest,
    GroupMemberOut,
    GroupMembersResponse,
    GroupOut,
    GroupShareOut,
    GroupSharesResponse,
    GroupUpdateRequest,
    TransferOwnershipRequest,
    TransferOwnershipResponse,
)
from app.wiki import acl, filesystem

router = APIRouter()
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Groups                                                                      #
# --------------------------------------------------------------------------- #


@router.get("/groups", response_model=GroupListResponse)
def list_groups(user: User = Depends(require_user)) -> GroupListResponse:
    """Admin sees all groups; a regular user sees only the ones they're in.

    Each group is enriched with member / page / folder counts for the
    groups list UI.
    """
    rows = (
        groups_repo.list_all() if user.is_admin else groups_repo.list_for_user(user.id)
    )
    member_counts = groups_repo.member_counts()
    grant_counts = acl.group_grant_counts()
    groups: list[GroupOut] = []
    for g in rows:
        grants = grant_counts.get(g["id"], {})
        groups.append(
            GroupOut(
                **g,
                member_count=member_counts.get(g["id"], 0),
                page_count=grants.get("pages", 0),
                folder_count=grants.get("folders", 0),
            )
        )
    return GroupListResponse(groups=groups)


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    req: GroupCreateRequest,
    actor: User = Depends(require_admin),
) -> GroupOut:
    try:
        gid = groups_repo.create(req.name, req.description, created_by_user_id=actor.id)
    except groups_repo.GroupNameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    g = groups_repo.get(gid)
    assert g is not None
    return GroupOut(**g)


@router.get("/groups/{group_id}", response_model=GroupMembersResponse)
def get_group(
    group_id: str,
    user: User = Depends(require_user),
) -> GroupMembersResponse:
    g = groups_repo.get(group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="not found")
    if not user.is_admin and user.id not in groups_repo.member_ids(group_id):
        raise HTTPException(status_code=403, detail="forbidden")
    members = [GroupMemberOut(**m) for m in groups_repo.members(group_id)]
    return GroupMembersResponse(group=GroupOut(**g), members=members)


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update_group(
    group_id: str,
    req: GroupUpdateRequest,
    _actor: User = Depends(require_admin),
) -> GroupOut:
    try:
        groups_repo.rename(group_id, req.name)
    except groups_repo.GroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except groups_repo.GroupNameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    g = groups_repo.get(group_id)
    assert g is not None
    return GroupOut(**g)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: str,
    _actor: User = Depends(require_admin),
) -> Response:
    if groups_repo.get(group_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    groups_repo.delete_group(group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/groups/{group_id}/shares", response_model=GroupSharesResponse)
def list_group_shares(
    group_id: str,
    _actor: User = Depends(require_admin),
) -> GroupSharesResponse:
    """Pages and folders shared with this group — for the admin group page's
    "shared resources" section, where access can be reviewed and revoked."""
    if groups_repo.get(group_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    shares = [GroupShareOut(**s) for s in acl.list_for_group(group_id)]
    return GroupSharesResponse(shares=shares)


@router.post("/groups/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_group_member(
    group_id: str,
    req: GroupMemberAddRequest,
    _actor: User = Depends(require_admin),
) -> Response:
    if groups_repo.get(group_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    if users_repo.get_by_id(req.user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    groups_repo.add_member(group_id, req.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/groups/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_group_member(
    group_id: str,
    user_id: str,
    _actor: User = Depends(require_admin),
) -> Response:
    if groups_repo.get(group_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    groups_repo.remove_member(group_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Wiki ACL                                                                    #
# --------------------------------------------------------------------------- #


def _can_share_path(path: str, user: User) -> bool:
    """Write-or-admin gate for ACL mutation endpoints. Anyone who can
    write a page can also share it; read-only callers can't reach
    these endpoints (which would let them probe the ACL of pages they
    can't see)."""
    return acl.can(user.id, user.is_admin, "write", path)


def _is_owner_or_admin(path: str, user: User) -> bool:
    """Tighter gate for actions that change who owns a page. Write-grant
    holders shouldn't be able to yank ownership away from the owner."""
    if user.is_admin:
        return True
    return acl.get_owner(path) == user.id


@router.get("/wiki/acl", response_model=AclListResponse)
def list_acl(
    path: str = Query(""),
    user: User = Depends(require_user),
) -> AclListResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        path = filesystem.safe_rel_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _can_share_path(path, user):
        raise HTTPException(status_code=403, detail="forbidden")
    owner_id = acl.get_owner(path)
    rows = acl.list_for_path(path)
    user_ids = {
        e["principal_id"]
        for e in rows
        if e["principal_kind"] == "user" and e.get("principal_id") is not None
    }
    if owner_id is not None:
        user_ids.add(owner_id)
    group_ids = {
        e["principal_id"]
        for e in rows
        if e["principal_kind"] == "group" and e.get("principal_id") is not None
    }
    users = users_repo.get_many(user_ids)
    groups = groups_repo.get_many(group_ids)
    entries = [_entry_out(e, users, groups) for e in rows]
    owner_email = owner_name = None
    if owner_id is not None:
        owner = users.get(owner_id)
        if owner is not None:
            owner_email, owner_name = owner["email"], owner["name"]
    return AclListResponse(
        path=path,
        owner_user_id=owner_id,
        owner_email=owner_email,
        owner_name=owner_name,
        entries=entries,
    )


def _entry_out(
    e: dict[str, Any],
    users: dict[str, dict[str, Any]],
    groups: dict[str, dict[str, Any]],
) -> AclEntryOut:
    """Serialize an ACL row, resolving the principal to a display label."""
    principal_email = principal_name = group_name = None
    pid = e.get("principal_id")
    if pid is not None and e["principal_kind"] == "user":
        u = users.get(pid)
        if u is not None:
            principal_email, principal_name = u["email"], u["name"]
    elif pid is not None and e["principal_kind"] == "group":
        g = groups.get(pid)
        if g is not None:
            group_name = g["name"]
    return AclEntryOut(
        **e,
        principal_email=principal_email,
        principal_name=principal_name,
        group_name=group_name,
    )


@router.post(
    "/wiki/acl",
    response_model=AclGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_acl_entry(
    req: AclGrantRequest,
    user: User = Depends(require_user),
) -> AclGrantResponse:
    try:
        path = filesystem.safe_rel_path(req.resource_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _can_share_path(path, user):
        raise HTTPException(status_code=403, detail="forbidden")

    if req.principal_kind == "user":
        if req.principal_id is None or users_repo.get_by_id(req.principal_id) is None:
            raise HTTPException(status_code=404, detail="user not found")
    elif req.principal_kind == "group":
        if req.principal_id is None or groups_repo.get(req.principal_id) is None:
            raise HTTPException(status_code=404, detail="group not found")

    try:
        eid = acl.grant(
            resource_kind=req.resource_kind,
            resource_path=path,
            principal_kind=req.principal_kind,
            principal_id=req.principal_id,
            permission=req.permission,
            granted_by_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AclGrantResponse(id=eid)


@router.delete("/wiki/acl/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_acl_entry(
    entry_id: str,
    user: User = Depends(require_user),
) -> Response:
    # We need the entry's resource_path to know whose owner-check to
    # apply. Fetch via the same code path the listing uses.

    with session() as s:
        e = s.get(AclEntry, entry_id)
        if e is None:
            raise HTTPException(status_code=404, detail="not found")
        path_for_check = e.resource_path
    if not _can_share_path(path_for_check, user):
        raise HTTPException(status_code=403, detail="forbidden")
    acl.revoke(entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Ownership transfer                                                          #
# --------------------------------------------------------------------------- #


@router.post("/wiki/transfer-ownership", response_model=TransferOwnershipResponse)
def transfer_ownership(
    req: TransferOwnershipRequest,
    user: User = Depends(require_user),
) -> TransferOwnershipResponse:
    try:
        path = filesystem.safe_rel_path(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _is_owner_or_admin(path, user):
        raise HTTPException(status_code=403, detail="forbidden")
    if (
        req.new_owner_user_id is not None
        and users_repo.get_by_id(req.new_owner_user_id) is None
    ):
        raise HTTPException(status_code=404, detail="user not found")
    acl.transfer_owner(path, req.new_owner_user_id)
    log.info(
        "ownership transferred path=%s new_owner=%s",
        path,
        req.new_owner_user_id or "<cleared>",
    )
    return TransferOwnershipResponse(
        path=path,
        owner_user_id=req.new_owner_user_id,
    )
