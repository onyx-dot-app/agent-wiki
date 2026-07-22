"""Permission fingerprints — the audience identity detection partitions on.

Detection never pairs pages across a visibility boundary: the *proposal alone*
would leak a restricted page's existence and title to whoever can see the
review surface. The mechanism is a **fingerprint** — a hash of a path's full
permission profile — computed here and used two ways:

- the runner partitions candidate paths by fingerprint, so pairing detectors
  (duplicate/merge) only ever compare pages with identical audiences;
- emitted proposals store it (``acl_fingerprint_before``), so staleness
  re-checks can notice "permissions changed since this was proposed".

The profile is the **expanded audience**, not the grant rows: group grants are
expanded to member user ids, so two pages restricted to the same people via
different groups fingerprint equal — and a group-membership change drifts the
fingerprint, which is exactly what staleness re-checks should notice. Write
implies read (as in ``acl.effective``); the owner is folded into both sets;
admins are excluded (a constant term in every profile). ``everyone`` grants
use the ``"*"`` sentinel and absorb the rest of their set — an explicit grant
adds nothing once everyone is in the audience.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from app.auth import groups as groups_repo
from app.wiki import acl

# The `everyone` principal in an expanded audience set.
EVERYONE = "*"


class PermissionProfile(BaseModel):
    """A path's expanded audience: who can read, who can write."""

    model_config = ConfigDict(frozen=True)

    read: frozenset[str]
    write: frozenset[str]

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"r": sorted(self.read), "w": sorted(self.write)},
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _normalize(principals: set[str]) -> frozenset[str]:
    """``everyone`` absorbs explicit principals — the audience is the same."""
    if EVERYONE in principals:
        return frozenset({EVERYONE})
    return frozenset(principals)


def profile_for_path(
    path: str, *, _group_members: dict[str, frozenset[str]] | None = None
) -> PermissionProfile:
    """The expanded audience for ``path``.

    Mirrors ``acl.effective``'s resolution, audience-side: page rows +
    ancestor-folder rows + owner, with the same implicit-public fallback (a
    completely unconfigured path — no owner, no applicable rows — is readable
    and writable by everyone). ``_group_members`` is a per-batch expansion
    cache; callers use :func:`fingerprints_for_paths` rather than passing it.
    """
    cache = _group_members if _group_members is not None else {}
    rows = acl.list_for_path(path)
    owner = acl.get_owner(path)

    if not rows and owner is None:
        # Implicit-public: unmanaged until the first owner stamp or grant.
        everyone = frozenset({EVERYONE})
        return PermissionProfile(read=everyone, write=everyone)

    read: set[str] = set()
    write: set[str] = set()
    for row in rows:
        kind, pid = row["principal_kind"], row["principal_id"]
        if kind == "everyone":
            principals: frozenset[str] = frozenset({EVERYONE})
        elif kind == "user":
            principals = frozenset({pid})
        else:  # group → expanded members
            if pid not in cache:
                cache[pid] = frozenset(groups_repo.member_ids(pid))
            principals = cache[pid]
        if row["permission"] == "write":
            write |= principals
        else:
            read |= principals

    if owner is not None:
        write.add(owner)
    read |= write  # write implies read
    return PermissionProfile(read=_normalize(read), write=_normalize(write))


def fingerprint_for_path(path: str) -> str:
    return profile_for_path(path).fingerprint()


def fingerprints_for_paths(paths: list[str]) -> dict[str, str]:
    """Batch fingerprints with one group expansion per group across the batch."""
    cache: dict[str, frozenset[str]] = {}
    return {
        p: profile_for_path(p, _group_members=cache).fingerprint() for p in paths
    }


def combined_fingerprint(paths: list[str]) -> str:
    """One fingerprint over a proposal's whole path-set — what
    ``acl_fingerprint_before`` stores. Deterministic in path order; equal iff
    every path's own profile is equal."""
    fps = fingerprints_for_paths(paths)
    payload = "\n".join(f"{p}={fps[p]}" for p in sorted(fps))
    return hashlib.sha256(payload.encode()).hexdigest()
