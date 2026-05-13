"""
Admin endpoint tests.
Non-admin (wrong or missing X-Admin-Password) must receive 403 Forbidden.
"""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient

# Must be set BEFORE importing server so ADMIN_PASSWORD is picked up at module load.
_TEST_PWD = "pytest_admin_secret"
os.environ["ADMIN_PASSWORD"] = _TEST_PWD

import server  # noqa: E402  (import after env setup)

GOOD = {"X-Admin-Password": _TEST_PWD}
BAD  = {"X-Admin-Password": "wrong_password"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("db") / "test.db")
    # Redirect DB and password to test values
    server.DB_PATH = db
    server.ADMIN_PASSWORD = _TEST_PWD
    server.init_db()
    with TestClient(server.app) as c:
        yield c


# ── GET /admin/configs ────────────────────────────────────────────────────────

class TestListConfigs:
    def test_no_auth_returns_403(self, client):
        r = client.get("/admin/configs")
        assert r.status_code == 403

    def test_wrong_auth_returns_403(self, client):
        r = client.get("/admin/configs", headers=BAD)
        assert r.status_code == 403

    def test_correct_auth_returns_200_list(self, client):
        r = client.get("/admin/configs", headers=GOOD)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── GET /admin/stats ──────────────────────────────────────────────────────────

class TestAdminStats:
    def test_no_auth_returns_403(self, client):
        r = client.get("/admin/stats")
        assert r.status_code == 403

    def test_wrong_auth_returns_403(self, client):
        r = client.get("/admin/stats", headers=BAD)
        assert r.status_code == 403

    def test_correct_auth_returns_200_list(self, client):
        r = client.get("/admin/stats", headers=GOOD)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── GET /admin/users ──────────────────────────────────────────────────────────

class TestListUsers:
    def test_no_auth_returns_403(self, client):
        r = client.get("/admin/users")
        assert r.status_code == 403

    def test_wrong_auth_returns_403(self, client):
        r = client.get("/admin/users", headers=BAD)
        assert r.status_code == 403

    def test_correct_auth_returns_200_list_with_admin(self, client):
        r = client.get("/admin/users", headers=GOOD)
        assert r.status_code == 200
        users = r.json()
        assert isinstance(users, list)
        assert any(u["role"] == "admin" for u in users)


# ── POST /admin/configs ───────────────────────────────────────────────────────

class TestCreateConfig:
    _payload = {
        "name": "test-config",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test-key",
        "provider": "anthropic",
        "models": "claude-sonnet-4-6",
        "price_input": 0.003,
        "price_output": 0.015,
        "is_active": 1,
    }

    def test_no_auth_returns_403(self, client):
        r = client.post("/admin/configs", json=self._payload)
        assert r.status_code == 403

    def test_wrong_auth_returns_403(self, client):
        r = client.post("/admin/configs", json=self._payload, headers=BAD)
        assert r.status_code == 403

    def test_correct_auth_creates_config(self, client):
        r = client.post("/admin/configs", json=self._payload, headers=GOOD)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == self._payload["name"]
        assert data["provider"] == self._payload["provider"]
        # API key is masked in response
        assert "****" in data["api_key"]


# ── Spec 1 新端点测试 ────────────────────────────────────

@pytest.fixture
def db_with_users(client):
    """在 client fixture 的 DB 上插入 admin + 2 个 user。返回他们的 token。
    每次 fixture 调用清干净 spec1 相关表 + 'a'/'b'/'c' 用户。"""
    with server.get_db() as conn:
        for t in ("api_keys", "sub_accounts", "accounts"):
            try: conn.execute(f"DELETE FROM {t}")
            except Exception: pass
        conn.execute("DELETE FROM user_tokens WHERE token LIKE 'tok-%'")
        conn.execute("DELETE FROM users WHERE username IN ('a','b','c')")
        conn.commit()
        now = "2026-01-01"
        for uname, role in [("a", "admin"), ("b", "user"), ("c", "user")]:
            conn.execute(
                "INSERT INTO users (username, password, role, created_at) VALUES (?, ?, ?, ?)",
                (uname, "x", role, now),
            )
        conn.commit()
        users = {
            r["username"]: r["id"]
            for r in conn.execute(
                "SELECT id, username FROM users WHERE username IN ('a','b','c')"
            ).fetchall()
        }
        tokens = {}
        for uname, uid in users.items():
            tok = f"tok-{uname}"
            conn.execute(
                "INSERT INTO user_tokens (user_id, token, created_at) VALUES (?, ?, ?)",
                (uid, tok, now),
            )
            tokens[uname] = tok
        conn.commit()
    return {"ids": users, "tokens": tokens}


def auth(token):
    return {"X-Token": token}


class TestGetAccounts:
    def test_no_token_401(self, client):
        r = client.get("/admin/accounts")
        assert r.status_code == 401

    def test_admin_sees_all(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        assert r.status_code == 200
        r = client.get("/admin/accounts", headers=auth(toks["a"]))
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_user_only_sees_own(self, client, db_with_users):
        toks = db_with_users["tokens"]
        client.post("/admin/accounts", headers=auth(toks["b"]),
                    json={"provider": "p1", "base_url": "http://x"})
        r = client.get("/admin/accounts", headers=auth(toks["c"]))
        assert r.json() == []
        r = client.get("/admin/accounts", headers=auth(toks["b"]))
        assert len(r.json()) == 1

    def test_manager_can_see(self, client, db_with_users):
        toks = db_with_users["tokens"]
        ids = db_with_users["ids"]
        client.post("/admin/accounts", headers=auth(toks["b"]),
                    json={"provider": "p1", "base_url": "http://x",
                          "manager_user_id": ids["c"]})
        r = client.get("/admin/accounts", headers=auth(toks["c"]))
        assert len(r.json()) == 1


class TestUpdateDeleteAccounts:
    def test_creator_can_update(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["b"]),
                       json={"provider": "p1", "base_url": "http://y", "team": "研发"})
        assert r.status_code == 200
        assert r.json()["base_url"] == "http://y"
        assert r.json()["team"] == "研发"

    def test_manager_cannot_update(self, client, db_with_users):
        toks = db_with_users["tokens"]
        ids = db_with_users["ids"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x",
                              "manager_user_id": ids["c"]})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["c"]),
                       json={"provider": "p2", "base_url": "http://z"})
        assert r.status_code == 403

    def test_admin_can_update_anyone(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["a"]),
                       json={"provider": "p1", "base_url": "http://y"})
        assert r.status_code == 200

    def test_delete_cascades_sub_accounts(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        client.post(f"/admin/accounts/{aid}/sub-accounts", headers=auth(toks["b"]),
                    json={"name": "sub1"})
        r = client.delete(f"/admin/accounts/{aid}", headers=auth(toks["b"]))
        assert r.status_code == 204
        with server.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM sub_accounts WHERE account_id=?", (aid,)
            ).fetchone()[0]
            assert n == 0


class TestSubAccounts:
    def test_create_sub_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]),
                        json={"name": "S1", "description": "d"})
        assert r.status_code == 200
        assert r.json()["name"] == "S1"

    def test_list_sub_accounts(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        for n in ("S1", "S2"):
            client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]), json={"name": n})
        r = client.get(f"/admin/accounts/{aid}/sub-accounts",
                       headers=auth(toks["b"]))
        assert len(r.json()) == 2

    def test_update_sub_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]), json={"name": "S1"})
        sid = r.json()["id"]
        r = client.put(f"/admin/sub-accounts/{sid}", headers=auth(toks["b"]),
                       json={"name": "S1-renamed"})
        assert r.json()["name"] == "S1-renamed"

    def test_other_user_cannot_create_in_my_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider": "p1", "base_url": "http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["c"]), json={"name": "S1"})
        assert r.status_code == 403
