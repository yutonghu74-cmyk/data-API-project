"""迁移脚本测试。每个 case 一个独立临时 DB。"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from migrations import v1_account_schema as mig  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """空 DB,有 users 表(模拟现状)。"""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TEXT NOT NULL
        );
        INSERT INTO users (username, password, role, created_at)
          VALUES ('admin', 'x', 'admin', '2026-01-01');
        CREATE TABLE api_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            provider TEXT NOT NULL,
            models TEXT DEFAULT '',
            manager TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    return str(db)


class TestStateDetection:
    def test_fresh_db_not_migrated(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        assert mig.is_already_migrated(conn) is False

    def test_view_present_means_migrated(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        conn.execute("DROP TABLE api_configs")
        conn.execute("CREATE VIEW api_configs AS SELECT 1 AS x")
        conn.commit()
        assert mig.is_already_migrated(conn) is True

    def test_partial_state_detected(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        conn.execute("CREATE TABLE accounts (id INTEGER)")
        conn.commit()
        # accounts 表存在但 api_configs 还是表 → 半成品
        assert mig.detect_partial_state(conn) is not None


class TestBackupAndPrecheck:
    def test_backup_creates_file(self, fresh_db):
        backup = mig.make_backup(fresh_db)
        assert os.path.exists(backup)
        # 备份是真实的 sqlite 文件
        c = sqlite3.connect(backup)
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert n == 1

    def test_precheck_passes_when_admin_exists(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        admin_id = mig.precheck_admin_user(conn)
        assert admin_id == 1

    def test_precheck_raises_without_admin(self, fresh_db):
        conn = sqlite3.connect(fresh_db)
        conn.execute("UPDATE users SET role='user' WHERE id=1")
        conn.commit()
        with pytest.raises(RuntimeError, match="role='admin'"):
            mig.precheck_admin_user(conn)


@pytest.fixture
def seeded_db(fresh_db):
    """fresh_db 基础上塞 3 行 api_configs。"""
    conn = sqlite3.connect(fresh_db)
    conn.executemany(
        "INSERT INTO api_configs (id, name, base_url, api_key, provider, models, manager, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, '2026-01-01', '2026-01-01')",
        [
            (1, "testv01", "https://yibuapi.com/v1", "sk-aaa", "一步", "gemini", ""),
            (2, "testv02", "yutongtong",             "sk-bbb", "yibu", "",       "胡宇彤"),
            (3, "testv03", "yutongtong",             "sk-ccc", "一步", "",       "hytt"),
        ],
    )
    conn.commit()
    conn.close()
    return fresh_db


class TestDoMigrate:
    def test_migrate_creates_three_accounts(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        n = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        assert n == 3

    def test_migrate_preserves_api_key_ids(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        rows = conn.execute(
            "SELECT id, name, api_key FROM api_keys ORDER BY id"
        ).fetchall()
        assert rows == [
            (1, "testv01", "sk-aaa"),
            (2, "testv02", "sk-bbb"),
            (3, "testv03", "sk-ccc"),
        ]

    def test_migrate_creates_default_sub_accounts(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        rows = conn.execute(
            "SELECT account_id, name FROM sub_accounts ORDER BY account_id"
        ).fetchall()
        assert rows == [(1, "默认"), (2, "默认"), (3, "默认")]

    def test_migrate_manager_username_match_becomes_fk(self, seeded_db):
        # 加一个 username='胡宇彤' 的用户
        conn = sqlite3.connect(seeded_db)
        conn.execute(
            "INSERT INTO users (username, password, role, created_at) "
            "VALUES ('胡宇彤','x','user','2026-01-01')"
        )
        conn.commit()
        conn.close()
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        row = conn.execute(
            "SELECT manager_user_id FROM accounts WHERE provider='yibu'"
        ).fetchone()
        # 胡宇彤 的 user id 是 2(admin=1, 胡宇彤=2)
        assert row[0] == 2

    def test_migrate_manager_not_in_users_becomes_null(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        rows = conn.execute(
            "SELECT provider, manager_user_id FROM accounts ORDER BY id"
        ).fetchall()
        # 没人叫 '' / '胡宇彤' / 'hytt' → 全 NULL
        assert all(r[1] is None for r in rows)

    def test_migrate_drops_old_api_configs_table(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='api_configs'"
        ).fetchone()
        assert row[0] == "view"


class TestMigrationIdempotencyAndVerify:
    def test_rerun_is_noop(self, seeded_db):
        mig.do_migrate(seeded_db)
        # 跑第二遍不应该抛异常
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 3

    def test_bridge_view_returns_expected_columns(self, seeded_db):
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM api_configs WHERE id=1").fetchone()
        cols = row.keys()
        for c in ("id", "name", "api_key", "provider", "base_url", "models",
                  "manager", "price_input", "price_output", "sub_account_name",
                  "is_active", "created_at", "updated_at"):
            assert c in cols, f"VIEW 缺列: {c}"
        assert row["name"] == "testv01"
        assert row["api_key"] == "sk-aaa"
        assert row["price_input"] == 0
        assert row["price_output"] == 0

    def test_partial_state_raises(self, fresh_db):
        # 模拟半成品:accounts 存在但 api_configs 还是表
        conn = sqlite3.connect(fresh_db)
        conn.execute("CREATE TABLE accounts (id INTEGER)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="半成品|半"):
            mig.do_migrate(fresh_db)

    def test_api_requests_config_id_still_resolves(self, seeded_db):
        # 插入一个 api_requests 引用 config_id=1
        conn = sqlite3.connect(seeded_db)
        conn.execute("""CREATE TABLE api_requests (
            id INTEGER PRIMARY KEY, config_id INTEGER, status TEXT DEFAULT 'pending'
        )""")
        conn.execute("INSERT INTO api_requests (id, config_id, status) VALUES (10, 1, 'approved')")
        conn.commit()
        conn.close()
        mig.do_migrate(seeded_db)
        conn = sqlite3.connect(seeded_db)
        row = conn.execute(
            "SELECT c.name FROM api_requests r JOIN api_configs c ON c.id=r.config_id WHERE r.id=10"
        ).fetchone()
        assert row[0] == "testv01"


class TestCLIFlags:
    def test_check_does_not_modify_db(self, seeded_db, capsys):
        mig.main(["--db", seeded_db, "--check"])
        # 跑完后 api_configs 仍是表
        conn = sqlite3.connect(seeded_db)
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='api_configs'"
        ).fetchone()
        assert row[0] == "table"

    def test_rollback_restores_backup(self, seeded_db):
        mig.make_backup(seeded_db)
        mig.do_migrate(seeded_db)
        # 现在 seeded_db 已迁移
        conn = sqlite3.connect(seeded_db)
        assert mig.is_already_migrated(conn)
        conn.close()
        # rollback 应该把 seeded_db 恢复
        mig.do_rollback(seeded_db)
        conn = sqlite3.connect(seeded_db)
        assert not mig.is_already_migrated(conn)
