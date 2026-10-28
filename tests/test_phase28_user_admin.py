# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 28 — user administration API (administrators only).

What this phase must prove: the three user-administration endpoints are
authenticated, restricted to the admin role (with every non-admin attempt
recorded in the audit chain), scoped to the caller's company, and that a
deactivation takes effect for live sessions immediately - the server
rejects the deactivated account's tokens and fresh logins alike. Unknown
and cross-tenant targets are deliberately conflated so responses never
reveal whether a foreign user id exists.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault

_TENANT_USERS = [
    (1, 1, "admin_a", "admin", 1, "AdminA#2026x"),
    (2, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", 1, "ClerkB#2026x"),
]


def _setup_two_tenants(db: ERPDatabase) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, cid, username, role, active, password in _TENANT_USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=1000)),
            )


def _make_client(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "useradmin.sqlite3"))
    _setup_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    return db, TestClient(create_erp_fastapi_app(db, vault))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _audit_actions(db: ERPDatabase) -> list:
    """All audit rows as tuples, in order (a set would collapse repeats)."""
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT actor_user_id, action_type, target_model, target_id FROM audit_events ORDER BY id"
        ).fetchall()
    return [(r["actor_user_id"], r["action_type"], r["target_model"], r["target_id"]) for r in rows]


def test_user_endpoints_require_authentication(tmp_path: Path) -> None:
    """No user administration leaks without a server-issued credential."""
    _, client = _make_client(tmp_path)
    assert client.get("/api/v1/users").status_code == 401
    assert (
        client.post(
            "/api/v1/users", json={"username": "x", "password": "Wh#2026x", "role": "clerk"}
        ).status_code
        == 401
    )
    assert client.post("/api/v1/users/2/deactivate").status_code == 401


def test_non_admin_is_refused_and_attempt_audited(tmp_path: Path) -> None:
    """Every non-admin attempt gets 403 and one audited block event."""
    db, client = _make_client(tmp_path)
    clerk = {"Authorization": f"Bearer {_login(client, 'clerk_a', 'Clerk#2026x')}"}

    listed = client.get("/api/v1/users", headers=clerk)
    assert listed.status_code == 403, listed.text
    created = client.post(
        "/api/v1/users",
        headers=clerk,
        json={"username": "intruder", "password": "Wh#2026x", "role": "clerk"},
    )
    assert created.status_code == 403, created.text
    deactivated = client.post("/api/v1/users/1/deactivate", headers=clerk)
    assert deactivated.status_code == 403, deactivated.text

    blocks = [e for e in _audit_actions(db) if e[1] == "SECURITY_USER_ADMIN_ACCESS_BLOCKED"]
    assert len(blocks) == 3, blocks
    assert all(e[0] == 2 for e in blocks), blocks  # clerk_a's id
    # Nothing actually changed: the list is unchanged and no user was created.
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    assert {u["username"] for u in client.get("/api/v1/users", headers=admin).json()["users"]} == {
        "admin_a",
        "clerk_a",
        "approver_a",
    }


def test_admin_lists_only_own_tenant_and_never_credential_material(tmp_path: Path) -> None:
    """The listing is tenant-scoped and exposes no password or token hashes."""
    _, client = _make_client(tmp_path)
    ha = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    list_a = client.get("/api/v1/users", headers=ha)
    assert list_a.status_code == 200, list_a.text
    body_a = list_a.json()
    assert body_a["company_id"] == 1
    assert {u["username"] for u in body_a["users"]} == {"admin_a", "clerk_a", "approver_a"}

    body_b = client.get("/api/v1/users", headers=hb).json()
    assert body_b["company_id"] == 2
    assert {u["username"] for u in body_b["users"]} == {"admin_b", "clerk_b"}

    for user in body_a["users"] + body_b["users"]:
        assert "password_hash" not in user
        assert "api_token_hash" not in user
        assert {"id", "username", "role", "is_active", "branch_id", "password_updated_at"} <= set(user)


def test_admin_creates_user_who_can_then_authenticate(tmp_path: Path) -> None:
    """Creation is audited (actor = acting admin) and the account can log in."""
    db, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}

    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "wh_a", "password": "Wh#2026x", "role": "warehouse"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["company_id"] == 1
    assert body["user"]["username"] == "wh_a"
    assert body["user"]["role"] == "warehouse"
    user_id = body["user"]["user_id"]

    listed = client.get("/api/v1/users", headers=admin).json()
    created_row = next(u for u in listed["users"] if u["username"] == "wh_a")
    assert created_row["is_active"] == 1
    assert listed["count"] == 4

    # The password was really hashed into a working credential.
    token = _login(client, "wh_a", "Wh#2026x")
    me = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "warehouse"

    events = _audit_actions(db)
    assert (1, "AUTH_USER_CREATED", "users", str(user_id)) in events


def test_create_rejects_duplicate_username(tmp_path: Path) -> None:
    """Usernames are unique; the schema violation surfaces as 409."""
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    dup = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "clerk_a", "password": "Wh#2026x", "role": "clerk"},
    )
    assert dup.status_code == 409, dup.text
    assert "clerk_a" in dup.json()["detail"]


def test_create_rejects_unknown_role(tmp_path: Path) -> None:
    """Only the validated role list is accepted; anything else is 422."""
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    bad = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "boss_a", "password": "Wh#2026x", "role": "superadmin"},
    )
    assert bad.status_code == 422, bad.text
    assert "superadmin" in bad.json()["detail"]


def test_create_rejects_short_password(tmp_path: Path) -> None:
    """The 8-character floor is enforced at the API boundary."""
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    short = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "shorty_a", "password": "Wh#2026", "role": "clerk"},
    )
    assert short.status_code == 422, short.text
    assert "8" in short.json()["detail"]


def test_create_rejects_blank_username(tmp_path: Path) -> None:
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    blank = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "   ", "password": "Wh#2026x", "role": "clerk"},
    )
    assert blank.status_code == 422, blank.text


def test_deactivation_takes_effect_for_live_and_fresh_sessions(tmp_path: Path) -> None:
    """A deactivated account's token is rejected and login is refused."""
    db, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}

    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "temp_a", "password": "Wh#2026x", "role": "clerk"},
    )
    target_id = created.json()["user"]["user_id"]
    live_token = _login(client, "temp_a", "Wh#2026x")
    live_headers = {"Authorization": f"Bearer {live_token}"}
    assert client.get("/api/v1/auth/session", headers=live_headers).status_code == 200

    deactivated = client.post(f"/api/v1/users/{target_id}/deactivate", headers=admin)
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] == 0

    # The live session dies at the next request...
    assert client.get("/api/v1/auth/session", headers=live_headers).status_code == 401
    # ...and no fresh login is possible either.
    refused = client.post("/api/v1/auth/token", json={"username": "temp_a", "password": "Wh#2026x"})
    assert refused.status_code == 401, refused.text

    assert (1, "AUTH_USER_DEACTIVATED", "users", str(target_id)) in _audit_actions(db)


def test_admin_cannot_deactivate_self(tmp_path: Path) -> None:
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    self_deactivate = client.post("/api/v1/users/1/deactivate", headers=admin)
    assert self_deactivate.status_code == 409, self_deactivate.text


def test_deactivating_an_inactive_user_conflicts(tmp_path: Path) -> None:
    db, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    assert client.post("/api/v1/users/2/deactivate", headers=admin).status_code == 200
    again = client.post("/api/v1/users/2/deactivate", headers=admin)
    assert again.status_code == 409, again.text
    assert "clerk_a" in again.json()["detail"]


def test_cross_tenant_and_unknown_targets_are_conflated(tmp_path: Path) -> None:
    """403 with the identical detail: no tenant- or id-existence oracle."""
    _, client = _make_client(tmp_path)
    admin = {"Authorization": f"Bearer {_login(client, 'admin_a', 'AdminA#2026x')}"}
    foreign = client.post("/api/v1/users/21/deactivate", headers=admin)  # tenant Beta clerk
    assert foreign.status_code == 403, foreign.text
    unknown = client.post("/api/v1/users/9999/deactivate", headers=admin)
    assert unknown.status_code == 403, unknown.text
    assert foreign.json()["detail"] == unknown.json()["detail"]

    # And the foreign clerk is untouched and still authenticated.
    hb = {"Authorization": f"Bearer {_login(client, 'clerk_b', 'ClerkB#2026x')}"}
    assert client.get("/api/v1/auth/session", headers=hb).status_code == 200
