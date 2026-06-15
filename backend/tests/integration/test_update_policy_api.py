"""HTTP tests for /api/update-policy — CRUD round-trip, folder cascade, and
the require_can write gate."""
from __future__ import annotations


def _strip_everyone(integration, path: str) -> None:
    """Revoke the seeded ``everyone`` page grants so ``path`` becomes private."""
    resp = integration.client.get(f"/api/wiki/acl?path={path}")
    assert resp.status_code == 200, resp.text
    for entry in resp.json()["entries"]:
        if entry["principal_kind"] == "everyone" and entry["resource_kind"] == "page":
            r = integration.client.delete(f"/api/wiki/acl/{entry['id']}")
            assert r.status_code == 204


def test_put_get_delete_roundtrip(integration):
    integration.signup(email="admin@x.com")  # auto-admin (first user)
    integration.put_doc("guide.md", "# Guide\n\nbody")

    resp = integration.client.put(
        "/api/update-policy",
        json={
            "path": "guide.md",
            "ingestion_auto_update_disabled": True,
            "update_instruction": "Keep it terse.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["explicit"]["kind"] == "page"
    assert body["explicit"]["ingestion_auto_update_disabled"] is True
    assert body["explicit"]["update_instruction"] == "Keep it terse."
    assert body["effective"]["ingestion_auto_update_disabled"] is True

    resp = integration.client.get("/api/update-policy?path=guide.md")
    assert resp.status_code == 200
    assert resp.json()["effective"]["update_instruction"] == "Keep it terse."

    resp = integration.client.delete("/api/update-policy?path=guide.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["explicit"] is None
    assert body["effective"]["ingestion_auto_update_disabled"] is False


def test_folder_policy_shows_in_doc_effective(integration):
    integration.signup(email="admin@x.com")
    integration.put_doc("team/guide.md", "# Guide")

    resp = integration.client.put(
        "/api/update-policy",
        json={"path": "team", "ingestion_auto_update_disabled": True},
    )
    assert resp.status_code == 200, resp.text

    resp = integration.client.get("/api/update-policy?path=team/guide.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["explicit"] is None  # no row on the doc itself
    assert body["effective"]["ingestion_auto_update_disabled"] is True  # inherited


def test_non_writer_forbidden(integration):
    integration.signup(email="admin@x.com")  # auto-admin, owns the page
    integration.put_doc("secret.md", "# Secret")
    _strip_everyone(integration, "secret.md")

    integration.signup(email="bob@x.com")  # session is now bob (non-admin)
    resp = integration.client.put(
        "/api/update-policy",
        json={"path": "secret.md", "ingestion_auto_update_disabled": True},
    )
    assert resp.status_code == 403


def test_non_admin_writer_allowed(integration):
    integration.signup(email="admin@x.com")
    integration.put_doc("open.md", "# Open")  # default-public: everyone read+write
    integration.signup(email="carol@x.com")  # non-admin, has everyone write

    resp = integration.client.put(
        "/api/update-policy",
        json={"path": "open.md", "update_instruction": "be brief"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["explicit"]["update_instruction"] == "be brief"
