"""Permission fingerprints — the audience identity detection partitions on.

The profile is the *expanded audience* (groups → member ids), so equal-audience
paths fingerprint equal regardless of how the grants are spelled, and a
group-membership change drifts the fingerprint.
"""
from __future__ import annotations

from app.auth import groups as groups_repo
from app.wiki import acl
from app.wiki.automanage import fingerprint
from app.wiki.automanage.fingerprint import EVERYONE, profile_for_path
from tests._seed import seed_user


def _grant(path: str, kind: str, pid: str | None, perm: str = "read") -> None:
    acl.grant(
        resource_kind="page" if path.endswith(".md") else "folder",
        resource_path=path,
        principal_kind=kind,
        principal_id=pid,
        permission=perm,
        granted_by_user_id=None,
    )


def test_unmanaged_path_is_implicit_public(tmp_db):
    p = profile_for_path("nowhere/unmanaged.md")
    assert p.read == frozenset({EVERYONE})
    assert p.write == frozenset({EVERYONE})
    # Two unmanaged paths share one audience → equal fingerprints.
    assert p.fingerprint() == profile_for_path("elsewhere/also.md").fingerprint()


def test_owner_is_folded_into_both_sets(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    acl.set_owner("docs/spec.md", uid)
    p = profile_for_path("docs/spec.md")
    assert p.read == frozenset({uid})
    assert p.write == frozenset({uid})


def test_write_implies_read(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    other = seed_user(uid="u2", email="u2@x.com")
    acl.set_owner("docs/spec.md", uid)
    _grant("docs/spec.md", "user", other, perm="write")
    p = profile_for_path("docs/spec.md")
    assert other in p.read and other in p.write


def test_everyone_absorbs_explicit_principals(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    reader = seed_user(uid="u2", email="u2@x.com")
    acl.set_owner("docs/spec.md", uid)
    _grant("docs/spec.md", "everyone", None)
    _grant("docs/spec.md", "user", reader)  # redundant next to everyone-read
    p = profile_for_path("docs/spec.md")
    assert p.read == frozenset({EVERYONE})  # audience unchanged by the grant
    assert p.write == frozenset({uid})


def test_folder_grants_cascade_to_pages(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    reader = seed_user(uid="u2", email="u2@x.com")
    acl.set_owner("team/notes.md", uid)
    _grant("team", "user", reader)  # folder grant, inherited by the page
    p = profile_for_path("team/notes.md")
    assert reader in p.read


def test_same_members_via_different_groups_fingerprint_equal(tmp_db):
    """Audience identity, not grant spelling: two pages restricted to the same
    people — one via a group, one via direct user grants — pair as equals."""
    owner = seed_user(uid="own", email="o@x.com")
    a = seed_user(uid="a", email="a@x.com")
    b = seed_user(uid="b", email="b@x.com")
    gid = groups_repo.create("team", None, owner)
    groups_repo.add_member(gid, a)
    groups_repo.add_member(gid, b)

    acl.set_owner("one.md", owner)
    _grant("one.md", "group", gid)
    acl.set_owner("two.md", owner)
    _grant("two.md", "user", a)
    _grant("two.md", "user", b)

    fps = fingerprint.fingerprints_for_paths(["one.md", "two.md"])
    assert fps["one.md"] == fps["two.md"]


def test_group_membership_change_drifts_the_fingerprint(tmp_db):
    owner = seed_user(uid="own", email="o@x.com")
    a = seed_user(uid="a", email="a@x.com")
    late = seed_user(uid="late", email="l@x.com")
    gid = groups_repo.create("team", None, owner)
    groups_repo.add_member(gid, a)
    acl.set_owner("one.md", owner)
    _grant("one.md", "group", gid)

    before = fingerprint.fingerprint_for_path("one.md")
    groups_repo.add_member(gid, late)  # no ACL row changed — only membership
    after = fingerprint.fingerprint_for_path("one.md")
    assert before != after


def test_different_audiences_fingerprint_differently(tmp_db):
    u1 = seed_user(uid="u1", email="u1@x.com")
    u2 = seed_user(uid="u2", email="u2@x.com")
    acl.set_owner("a.md", u1)
    acl.set_owner("b.md", u2)
    assert (
        fingerprint.fingerprint_for_path("a.md")
        != fingerprint.fingerprint_for_path("b.md")
    )


def test_combined_fingerprint_covers_every_path(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    acl.set_owner("a.md", uid)
    combined_before = fingerprint.combined_fingerprint(["a.md", "b.md"])
    # Managing the second path changes the set's combined fingerprint.
    acl.set_owner("b.md", uid)
    assert fingerprint.combined_fingerprint(["a.md", "b.md"]) != combined_before
    # Order-insensitive.
    assert fingerprint.combined_fingerprint(
        ["b.md", "a.md"]
    ) == fingerprint.combined_fingerprint(["a.md", "b.md"])
