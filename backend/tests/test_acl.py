"""Permission resolver, ACL grants, ownership, and lifecycle hooks.

Covers ``app.wiki.acl`` exhaustively — every branch of the resolver,
every shape of grant, the bootstrap walk, and the rename/delete hooks.
``app.auth.groups`` is exercised end-to-end through the same tests since
the resolver depends on group membership.
"""
from __future__ import annotations

import pytest

from app.auth import groups as groups_repo
from app.wiki import acl
from tests._seed import seed_user


# --------------------------------------------------------------------------- #
# Groups repo                                                                 #
# --------------------------------------------------------------------------- #


def test_group_create_and_membership(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")

    gid = groups_repo.create("eng", "engineering team", created_by_user_id=admin)
    assert gid.startswith("grp_")

    groups_repo.add_member(gid, alice)
    groups_repo.add_member(gid, bob)
    # Idempotent re-add.
    groups_repo.add_member(gid, alice)

    assert sorted(groups_repo.member_ids(gid)) == sorted([alice, bob])
    assert sorted(groups_repo.group_ids_for_user(alice)) == [gid]
    assert groups_repo.group_ids_for_user("u_unknown") == []

    groups_repo.remove_member(gid, alice)
    assert sorted(groups_repo.member_ids(gid)) == [bob]


def test_group_name_uniqueness(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    groups_repo.create("eng", None, created_by_user_id=admin)
    with pytest.raises(groups_repo.GroupNameTakenError):
        groups_repo.create("eng", None, created_by_user_id=admin)


def test_group_member_requires_existing_user_and_group(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    gid = groups_repo.create("eng", None, created_by_user_id=admin)
    with pytest.raises(groups_repo.UserNotFoundError):
        groups_repo.add_member(gid, "u_nope")
    with pytest.raises(groups_repo.GroupNotFoundError):
        groups_repo.add_member("grp_nope", admin)


# --------------------------------------------------------------------------- #
# Resolver — admin / owner short-circuits                                     #
# --------------------------------------------------------------------------- #


def test_admin_bypasses_all_checks(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    # No ACLs at all — admin still has full access.
    assert acl.effective(admin, True, "projects/foo.md") == {"read", "write"}


def test_owner_has_full_access_without_acl_rows(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.set_owner("docs/spec.md", alice)
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}


def test_anonymous_has_no_access_when_path_is_managed(tmp_db):
    # An unmanaged page (no owner, no ACL rows) is implicit-public —
    # this covers pages predating the feature and tests that bypass the
    # lifecycle hook. To test the deny path, we mark the path as managed
    # by setting an owner.
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.set_owner("docs/spec.md", alice)
    assert acl.effective(None, False, "docs/spec.md") == set()
    # Add an everyone read — anonymous now matches.
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=None,
    )
    assert acl.effective(None, False, "docs/spec.md") == {"read"}


def test_unmanaged_page_is_implicit_public(tmp_db):
    """Pages with no owner and no ACL rows anywhere on the path are
    treated as public — covers pre-feature seeded pages."""
    assert acl.effective(None, False, "legacy/page.md") == {"read", "write"}
    alice = seed_user(uid="u_alice", email="alice@x.com")
    assert acl.effective(alice, False, "legacy/page.md") == {"read", "write"}


# --------------------------------------------------------------------------- #
# Resolver — page-level grants                                                #
# --------------------------------------------------------------------------- #


def test_user_grant_on_page(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )
    assert acl.effective(bob, False, "docs/spec.md") == {"read"}
    # Alice has nothing on that path (she's not the owner here).
    assert acl.effective(alice, False, "docs/spec.md") == set()


def test_write_implies_read(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="write",
        granted_by_user_id=None,
    )
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}


def test_grant_is_idempotent(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    e1 = acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    e2 = acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    assert e1 == e2


# --------------------------------------------------------------------------- #
# Resolver — group grants                                                     #
# --------------------------------------------------------------------------- #


def test_group_grant_propagates_to_members(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    gid = groups_repo.create("eng", None, created_by_user_id=admin)
    groups_repo.add_member(gid, alice)

    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="group",
        principal_id=gid,
        permission="write",
        granted_by_user_id=admin,
    )
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}
    assert acl.effective(bob, False, "docs/spec.md") == set()


# --------------------------------------------------------------------------- #
# Resolver — folder cascade                                                   #
# --------------------------------------------------------------------------- #


def test_folder_grant_cascades_to_descendants(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.grant(
        resource_kind="folder",
        resource_path="docs",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    assert acl.effective(alice, False, "docs/spec.md") == {"read"}
    assert acl.effective(alice, False, "docs/sub/nested.md") == {"read"}
    # Sibling folder is *managed* (an unrelated owner is set), so the
    # implicit-public fallback doesn't apply and Alice has no access.
    acl.set_owner("other/spec.md", bob)
    assert acl.effective(alice, False, "other/spec.md") == set()


def test_root_folder_grant_matches_everything(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.grant(
        resource_kind="folder",
        resource_path="",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    assert acl.effective(alice, False, "top.md") == {"read"}
    assert acl.effective(alice, False, "deep/nest/page.md") == {"read"}


def test_grants_are_unioned_across_levels(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    # Folder grants read; page grants write. Effective should have both,
    # with write implying read again (no double-count, just both perms).
    acl.grant(
        resource_kind="folder",
        resource_path="docs",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="write",
        granted_by_user_id=None,
    )
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}


# --------------------------------------------------------------------------- #
# can() convenience wrapper                                                   #
# --------------------------------------------------------------------------- #


def test_can_returns_bool(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    assert acl.can(alice, False, "read", "docs/spec.md") is True
    assert acl.can(alice, False, "write", "docs/spec.md") is False
    # Admin always allowed.
    assert acl.can(None, True, "write", "docs/spec.md") is True


# --------------------------------------------------------------------------- #
# Lifecycle hooks                                                             #
# --------------------------------------------------------------------------- #


def test_on_page_created_seeds_owner_and_everyone_grants(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.on_page_created("docs/spec.md", owner_user_id=alice)

    assert acl.get_owner("docs/spec.md") == alice
    grants = acl.list_for_path("docs/spec.md")
    perms = {g["permission"] for g in grants if g["principal_kind"] == "everyone"}
    assert perms == {"read", "write"}


def test_on_page_created_is_idempotent(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.on_page_created("docs/spec.md", owner_user_id=alice)
    acl.on_page_created("docs/spec.md", owner_user_id=alice)
    grants = [g for g in acl.list_for_path("docs/spec.md") if g["principal_kind"] == "everyone"]
    assert len(grants) == 2  # one read + one write, not duplicated.


def test_on_page_deleted_drops_owner_and_acls(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.on_page_created("docs/spec.md", owner_user_id=alice)
    assert acl.get_owner("docs/spec.md") == alice

    acl.on_page_deleted("docs/spec.md")
    assert acl.get_owner("docs/spec.md") is None
    assert acl.list_for_path("docs/spec.md") == []


def test_on_path_moved_rewrites_page_owner_and_acls(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.on_page_created("old/spec.md", owner_user_id=alice)
    acl.on_path_moved([("old/spec.md", "new/spec.md")])

    assert acl.get_owner("old/spec.md") is None
    assert acl.get_owner("new/spec.md") == alice
    new_grants = acl.list_for_path("new/spec.md")
    everyone_grants = [g for g in new_grants if g["principal_kind"] == "everyone"]
    assert len(everyone_grants) == 2
    assert all(g["resource_path"] == "new/spec.md" for g in everyone_grants)


def test_on_path_moved_rewrites_folder_acls_for_directory_rename(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    # Folder grant on the old folder.
    acl.grant(
        resource_kind="folder",
        resource_path="old",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    # Two pages under it (simulating a directory move).
    acl.on_page_created("old/a.md", owner_user_id=alice)
    acl.on_page_created("old/b.md", owner_user_id=alice)
    acl.on_path_moved([("old/a.md", "new/a.md"), ("old/b.md", "new/b.md")])

    # Folder grant should now apply at "new".
    assert acl.effective(alice, False, "new/a.md") >= {"read"}
    # Old folder no longer matches — set an owner on the test path so the
    # implicit-public fallback doesn't kick in for this assertion.
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.set_owner("old/whatever.md", alice)
    assert acl.effective(bob, False, "old/whatever.md") == set()


# --------------------------------------------------------------------------- #
# Path canonicalization                                                       #
# --------------------------------------------------------------------------- #


def test_grant_canonicalizes_paths(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    # Trailing slash on folder gets stripped.
    acl.grant(
        resource_kind="folder",
        resource_path="docs/",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    # Leading slash also stripped.
    acl.grant(
        resource_kind="folder",
        resource_path="/other",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=None,
    )
    assert acl.effective(alice, False, "docs/x.md") == {"read"}
    assert acl.effective(alice, False, "other/x.md") == {"read"}


def test_grant_rejects_non_md_page_path(tmp_db):
    with pytest.raises(ValueError):
        acl.grant(
            resource_kind="page",
            resource_path="docs/no-ext",
            principal_kind="everyone",
            principal_id=None,
            permission="read",
            granted_by_user_id=None,
        )


def test_grant_validates_principal_alignment(tmp_db):
    # everyone with non-NULL principal_id is invalid.
    with pytest.raises(ValueError):
        acl.grant(
            resource_kind="page",
            resource_path="docs/x.md",
            principal_kind="everyone",
            principal_id="some_id",
            permission="read",
            granted_by_user_id=None,
        )
    # user with NULL principal_id is invalid.
    with pytest.raises(ValueError):
        acl.grant(
            resource_kind="page",
            resource_path="docs/x.md",
            principal_kind="user",
            principal_id=None,
            permission="read",
            granted_by_user_id=None,
        )


# --------------------------------------------------------------------------- #
# Bulk visibility filter                                                      #
# --------------------------------------------------------------------------- #


def test_filter_paths_in_python(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.on_page_created("public.md", owner_user_id=None)  # everyone read+write
    acl.set_owner("private.md", bob)  # alice has no access
    # Alice can see public, not private.
    assert acl.filter_paths_in_python(alice, False, ["public.md", "private.md"]) == [
        "public.md"
    ]
    # Admin sees all.
    assert acl.filter_paths_in_python(alice, True, ["public.md", "private.md"]) == [
        "public.md",
        "private.md",
    ]


# --------------------------------------------------------------------------- #
# Group membership lifecycle                                                  #
# --------------------------------------------------------------------------- #


def test_group_membership_change_reflects_in_effective(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    alice = seed_user(uid="u_alice", email="alice@x.com")
    gid = groups_repo.create("eng", None, created_by_user_id=admin)
    # Mark the path managed (set an owner) so the implicit-public fallback
    # doesn't mask the membership-driven access change.
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.set_owner("docs/spec.md", bob)
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="group",
        principal_id=gid,
        permission="read",
        granted_by_user_id=admin,
    )

    # Alice not in the group yet — no access.
    assert acl.effective(alice, False, "docs/spec.md") == set()
    # Add to group.
    groups_repo.add_member(gid, alice)
    assert acl.effective(alice, False, "docs/spec.md") == {"read"}
    # Remove from group.
    groups_repo.remove_member(gid, alice)
    assert acl.effective(alice, False, "docs/spec.md") == set()


def test_empty_group_grant_grants_no_one(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    gid = groups_repo.create("eng", None, created_by_user_id=admin)
    # Mark managed so implicit-public is off.
    acl.set_owner("docs/spec.md", bob)
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="group",
        principal_id=gid,
        permission="read",
        granted_by_user_id=admin,
    )
    # No members → no one matches the group grant.
    assert acl.effective(alice, False, "docs/spec.md") == set()


def test_self_grant_is_redundant_but_valid(tmp_db):
    """Granting access to the page's owner is a no-op — they already
    have full access — but it shouldn't error or duplicate."""
    alice = seed_user(uid="u_alice", email="alice@x.com")
    acl.set_owner("docs/spec.md", alice)
    eid = acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=alice,
        permission="read",
        granted_by_user_id=alice,
    )
    assert eid.startswith("acl_")
    # Owner still has full access.
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}


# --------------------------------------------------------------------------- #
# Implicit-public boundary                                                    #
# --------------------------------------------------------------------------- #


def test_implicit_public_disappears_once_path_is_managed(tmp_db):
    bob = seed_user(uid="u_bob", email="bob@x.com")
    # Before any rows: implicit-public.
    assert acl.effective(bob, False, "x.md") == {"read", "write"}
    # Add an unrelated grant on the same path (e.g. for someone else).
    other = seed_user(uid="u_other", email="other@x.com")
    acl.grant(
        resource_kind="page",
        resource_path="x.md",
        principal_kind="user",
        principal_id=other,
        permission="read",
        granted_by_user_id=None,
    )
    # Path is now managed; Bob falls back to explicit rules and matches none.
    assert acl.effective(bob, False, "x.md") == set()
    # The other user still has their granted read.
    assert acl.effective(other, False, "x.md") == {"read"}


def test_managed_via_owner_only_makes_path_private(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.set_owner("docs/spec.md", alice)
    # Owner has full access.
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}
    # Anyone else with no grants — and the implicit-public is off because
    # there's an owner row — has nothing.
    assert acl.effective(bob, False, "docs/spec.md") == set()


def test_list_for_path_orders_page_then_folder_deepest_first(tmp_db):
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    # Folder grant at root, mid, deep — plus a page grant.
    acl.grant(
        resource_kind="folder", resource_path="",
        principal_kind="everyone", principal_id=None, permission="read",
        granted_by_user_id=admin,
    )
    acl.grant(
        resource_kind="folder", resource_path="docs",
        principal_kind="everyone", principal_id=None, permission="read",
        granted_by_user_id=admin,
    )
    acl.grant(
        resource_kind="folder", resource_path="docs/sub",
        principal_kind="everyone", principal_id=None, permission="read",
        granted_by_user_id=admin,
    )
    acl.grant(
        resource_kind="page", resource_path="docs/sub/x.md",
        principal_kind="everyone", principal_id=None, permission="read",
        granted_by_user_id=admin,
    )
    rows = acl.list_for_path("docs/sub/x.md")
    # Page row(s) come before folder rows.
    assert rows[0]["resource_kind"] == "page"
    folder_paths = [r["resource_path"] for r in rows if r["resource_kind"] == "folder"]
    assert folder_paths == ["docs/sub", "docs", ""]


# --------------------------------------------------------------------------- #
# Inert grants after principal deletion                                       #
# --------------------------------------------------------------------------- #


def test_grants_to_deleted_group_become_inert(tmp_db):
    """``acl_entries.principal_id`` is not a real foreign key (one column
    can't FK to two tables), so deleting a group leaves grant rows in
    place. They simply don't match anyone's group_ids and have no effect.
    """
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    gid = groups_repo.create("eng", None, created_by_user_id=admin)
    groups_repo.add_member(gid, alice)
    acl.set_owner("docs/spec.md", bob)
    acl.grant(
        resource_kind="page", resource_path="docs/spec.md",
        principal_kind="group", principal_id=gid, permission="read",
        granted_by_user_id=admin,
    )
    assert acl.effective(alice, False, "docs/spec.md") == {"read"}

    groups_repo.delete_group(gid)
    # The grant row may still exist, but Alice's group_ids no longer
    # include the deleted group, so she resolves to no access.
    assert acl.effective(alice, False, "docs/spec.md") == set()


def test_deleting_owner_user_clears_owner_row(tmp_db):
    """``ON DELETE SET NULL`` keeps the wiki_owners row but clears the
    owner — page falls back to ACL rules."""
    from app.auth import users as users_repo
    from app.db.models import User
    from app.db.session import session

    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    acl.set_owner("docs/spec.md", alice)
    acl.grant(
        resource_kind="page", resource_path="docs/spec.md",
        principal_kind="user", principal_id=bob, permission="read",
        granted_by_user_id=alice,
    )
    # Sanity: Alice as owner has full access.
    assert acl.effective(alice, False, "docs/spec.md") == {"read", "write"}

    users_repo.delete(alice)
    # The wiki_owners row exists but owner_user_id is NULL.
    assert acl.get_owner("docs/spec.md") is None
    # Page is still managed (an ACL row points at Bob), so a stranger
    # gets nothing — but Bob still has his granted read.
    eve = seed_user(uid="u_eve", email="eve@x.com")
    assert acl.effective(eve, False, "docs/spec.md") == set()
    assert acl.effective(bob, False, "docs/spec.md") == {"read"}
    # Sanity that the User row really is gone.
    with session() as s:
        assert s.get(User, alice) is None


def test_visible_paths_filter_against_db(tmp_db):
    """Exercise the SQL predicate end-to-end against documents_fts paths."""
    from sqlalchemy import select as sa_select

    from app.db.fts import upsert_document
    from app.db.models import DocumentFts
    from app.db.session import session

    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")

    # Public doc — Alice can see.
    acl.on_page_created("public.md", owner_user_id=None)
    upsert_document("d_public", "public.md", "Public", "hello")

    # Bob's private doc — Alice cannot see.
    acl.set_owner("private.md", bob)
    upsert_document("d_private", "private.md", "Private", "secret")

    pred = acl.visible_paths_filter(alice, False, DocumentFts.path)
    with session() as s:
        rows = s.scalars(
            sa_select(DocumentFts.path).where(pred).order_by(DocumentFts.path)
        ).all()
    assert list(rows) == ["public.md"]

    # Admin filter is universal-true.
    pred_admin = acl.visible_paths_filter("u_admin", True, DocumentFts.path)
    with session() as s:
        rows = s.scalars(
            sa_select(DocumentFts.path).where(pred_admin).order_by(DocumentFts.path)
        ).all()
    assert list(rows) == ["private.md", "public.md"]
