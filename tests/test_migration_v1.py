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
