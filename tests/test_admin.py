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
