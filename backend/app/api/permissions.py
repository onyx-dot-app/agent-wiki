"""Group + ACL HTTP endpoints.

Splits naturally into two surfaces:

  * ``/api/groups`` — admin-managed CRUD over ``Group``/``GroupMember``.
    Non-admins can ``GET /api/groups`` to see the groups they belong to;
    everything else (create, delete, membership edits) is admin-only in
    v1. See ``permissions/permissions.md`` for the open question about
    self-service group creation.
  * ``/api/wiki/acl`` and ``/api/wiki/transfer-ownership`` — per-page
    permission editing. Owner (or admin) may grant/revoke access to a
    page they own, or hand the page to another user.

All endpoints return the standard ``{"error": "..."}`` shape on failure
via ``app.models._helpers.error``.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from app.auth import admin_required, current_user, login_required
from app.auth import groups as groups_repo
from app.auth import users as users_repo
from app.models._helpers import error, parse_body
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
    TransferOwnershipRequest,
    TransferOwnershipResponse,
)
from app.wiki import acl, filesystem

bp = Blueprint("permissions", __name__)
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Groups                                                                      #
# --------------------------------------------------------------------------- #


@bp.get("/groups")
@login_required
def list_groups():
    """Admin sees all groups; a regular user sees only the ones they're in."""
    user = current_user()
    assert user is not None
    rows = groups_repo.list_all() if user.is_admin else groups_repo.list_for_user(user.id)
    return jsonify(GroupListResponse(
        groups=[GroupOut(**g) for g in rows]
    ).model_dump())


@bp.post("/groups")
@admin_required
def create_group():
    req = parse_body(GroupCreateRequest, request.get_json(silent=True))
    user = current_user()
    assert user is not None
    try:
        gid = groups_repo.create(req.name, req.description, created_by_user_id=user.id)
    except groups_repo.GroupNameTakenError as exc:
        return error(str(exc), 409)
    g = groups_repo.get(gid)
    assert g is not None
    return jsonify(GroupOut(**g).model_dump()), 201


@bp.get("/groups/<group_id>")
@login_required
def get_group(group_id: str):
    g = groups_repo.get(group_id)
    if g is None:
        return error("not found", 404)
    user = current_user()
    assert user is not None
    if not user.is_admin and user.id not in groups_repo.member_ids(group_id):
        return error("forbidden", 403)
    members = [GroupMemberOut(**m) for m in groups_repo.members(group_id)]
    return jsonify(GroupMembersResponse(
        group=GroupOut(**g), members=members
    ).model_dump())


@bp.delete("/groups/<group_id>")
@admin_required
def delete_group(group_id: str):
    if groups_repo.get(group_id) is None:
        return error("not found", 404)
    groups_repo.delete_group(group_id)
    return ("", 204)


@bp.post("/groups/<group_id>/members")
@admin_required
def add_group_member(group_id: str):
    if groups_repo.get(group_id) is None:
        return error("not found", 404)
    req = parse_body(GroupMemberAddRequest, request.get_json(silent=True))
    if users_repo.get_by_id(req.user_id) is None:
        return error("user not found", 404)
    groups_repo.add_member(group_id, req.user_id)
    return ("", 204)


@bp.delete("/groups/<group_id>/members/<user_id>")
@admin_required
def remove_group_member(group_id: str, user_id: str):
    if groups_repo.get(group_id) is None:
        return error("not found", 404)
    groups_repo.remove_member(group_id, user_id)
    return ("", 204)


# --------------------------------------------------------------------------- #
# Wiki ACL                                                                    #
# --------------------------------------------------------------------------- #


def _can_share_path(path: str) -> bool:
    """Write-or-admin gate for ACL mutation endpoints.

    Anyone who can write a page can also share it / change its access
    scope — that includes the owner (writes implicitly), explicit
    write-grant holders, and admins. Read-only callers can't reach
    these endpoints, which prevents probing the ACL of pages they
    can't see.
    """
    user = current_user()
    if user is None:
        return False
    return acl.can(user.id, user.is_admin, "write", path)


def _is_owner_or_admin(path: str) -> bool:
    """Tighter gate for actions that change who owns a page. Write-grant
    holders shouldn't be able to yank ownership away from the owner."""
    user = current_user()
    if user is None:
        return False
    if user.is_admin:
        return True
    return acl.get_owner(path) == user.id


@bp.get("/wiki/acl")
@login_required
def list_acl():
    raw = request.args.get("path", "")
    if not raw:
        return error("path required", 400)
    try:
        path = filesystem.safe_rel_path(raw)
    except ValueError as exc:
        return error(str(exc), 400)
    user = current_user()
    assert user is not None
    # Anyone who can write the page can see/manage its ACL. Read-only
    # callers are denied so they can't probe who else has access to a
    # page they can't edit.
    if not _can_share_path(path):
        return error("forbidden", 403)
    entries = [AclEntryOut(**e) for e in acl.list_for_path(path)]
    return jsonify(AclListResponse(
        path=path,
        owner_user_id=acl.get_owner(path),
        entries=entries,
    ).model_dump())


@bp.post("/wiki/acl")
@login_required
def create_acl_entry():
    req = parse_body(AclGrantRequest, request.get_json(silent=True))
    try:
        path = filesystem.safe_rel_path(req.resource_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not _can_share_path(path):
        return error("forbidden", 403)

    # Validate principal_id refers to a real row before the resolver
    # accepts a grant we'd never be able to evaluate.
    if req.principal_kind == "user":
        if req.principal_id is None or users_repo.get_by_id(req.principal_id) is None:
            return error("user not found", 404)
    elif req.principal_kind == "group":
        if req.principal_id is None or groups_repo.get(req.principal_id) is None:
            return error("group not found", 404)

    user = current_user()
    try:
        eid = acl.grant(
            resource_kind=req.resource_kind,
            resource_path=path,
            principal_kind=req.principal_kind,
            principal_id=req.principal_id,
            permission=req.permission,
            granted_by_user_id=user.id if user else None,
        )
    except ValueError as exc:
        return error(str(exc), 400)
    return jsonify(AclGrantResponse(id=eid).model_dump()), 201


@bp.delete("/wiki/acl/<entry_id>")
@login_required
def delete_acl_entry(entry_id: str):
    # We need the entry's resource_path to know whose owner-check to
    # apply. Fetch via the same code path the listing uses.
    from app.db.models import AclEntry
    from app.db.session import session

    with session() as s:
        e = s.get(AclEntry, entry_id)
        if e is None:
            return error("not found", 404)
        path_for_check = e.resource_path
    if not _can_share_path(path_for_check):
        return error("forbidden", 403)
    acl.revoke(entry_id)
    return ("", 204)


# --------------------------------------------------------------------------- #
# Ownership transfer                                                          #
# --------------------------------------------------------------------------- #


@bp.post("/wiki/transfer-ownership")
@login_required
def transfer_ownership():
    req = parse_body(TransferOwnershipRequest, request.get_json(silent=True))
    try:
        path = filesystem.safe_rel_path(req.path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not _is_owner_or_admin(path):
        return error("forbidden", 403)
    if req.new_owner_user_id is not None and users_repo.get_by_id(req.new_owner_user_id) is None:
        return error("user not found", 404)
    acl.transfer_owner(path, req.new_owner_user_id)
    log.info(
        "ownership transferred path=%s new_owner=%s",
        path, req.new_owner_user_id or "<cleared>",
    )
    return jsonify(TransferOwnershipResponse(
        path=path, owner_user_id=req.new_owner_user_id,
    ).model_dump())
