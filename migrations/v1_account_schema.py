#!/usr/bin/env python3
"""
Spec 1 迁移:api_configs (1 表多列) → accounts + sub_accounts + api_keys + 桥接 VIEW。

用法:
    python migrations/v1_account_schema.py            # 真跑
    python migrations/v1_account_schema.py --check    # 干跑校验
    python migrations/v1_account_schema.py --rollback # 从最近备份还原
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

DB_PATH = "admin.db"
BACKUP_PREFIX = "admin.db.pre-v1-"

DDL_ACCOUNTS = """
CREATE TABLE accounts (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  provider             TEXT NOT NULL,
  base_url             TEXT NOT NULL,
  provider_backend_url TEXT DEFAULT '',
  quota_total_path     TEXT DEFAULT '',
  balance_path         TEXT DEFAULT '',
  cost_path            TEXT DEFAULT '',
  manager_user_id      INTEGER REFERENCES users(id),
  team                 TEXT DEFAULT '',
  created_by           INTEGER NOT NULL REFERENCES users(id),
  models               TEXT DEFAULT '',
  is_active            INTEGER DEFAULT 1,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);
CREATE INDEX idx_accounts_created_by ON accounts(created_by);
CREATE INDEX idx_accounts_manager    ON accounts(manager_user_id);
CREATE INDEX idx_accounts_team       ON accounts(team);
"""

DDL_SUB_ACCOUNTS = """
DROP TABLE IF EXISTS sub_accounts;
CREATE TABLE sub_accounts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_sub_account ON sub_accounts(account_id);
"""

DDL_API_KEYS = """
CREATE TABLE api_keys (
  id              INTEGER PRIMARY KEY,
  sub_account_id  INTEGER NOT NULL REFERENCES sub_accounts(id) ON DELETE RESTRICT,
  name            TEXT NOT NULL,
  api_key         TEXT NOT NULL,
  is_active       INTEGER DEFAULT 1,
  exhausted       INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL
);
CREATE INDEX idx_apikeys_sub ON api_keys(sub_account_id);
"""

VIEW_SQL = """
CREATE VIEW api_configs AS
SELECT
  k.id                     AS id,
  k.name                   AS name,
  k.api_key                AS api_key,
  a.provider               AS provider,
  a.base_url               AS base_url,
  a.models                 AS models,
  COALESCE(u.username, '') AS manager,
  0                        AS price_input,
  0                        AS price_output,
  ''                       AS sub_account_name,
  k.is_active              AS is_active,
  k.created_at             AS created_at,
  k.created_at             AS updated_at
FROM api_keys k
JOIN sub_accounts s ON s.id = k.sub_account_id
JOIN accounts a     ON a.id = s.account_id
LEFT JOIN users u   ON u.id = a.manager_user_id;
"""


def is_already_migrated(conn: sqlite3.Connection) -> bool:
    """api_configs 已经是 VIEW → True"""
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='api_configs'"
    ).fetchone()
    return bool(row and row[0] == "view")


def detect_partial_state(conn: sqlite3.Connection) -> str | None:
    """检测半成品:返回错误描述或 None。"""
    has_accounts = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='accounts'"
    ).fetchone() is not None
    configs_type_row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='api_configs'"
    ).fetchone()
    configs_is_table = configs_type_row and configs_type_row[0] == "table"
    if has_accounts and configs_is_table:
        return "accounts 表存在但 api_configs 仍是表 — 上次迁移失败一半"
    return None


def make_backup(db_path: str) -> str:
    """复制 DB 到 .pre-v1-{timestamp}.db,返回备份路径。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{BACKUP_PREFIX}{ts}.db"
    backup_path = os.path.join(os.path.dirname(db_path) or ".", backup)
    shutil.copy2(db_path, backup_path)
    return backup_path


def precheck_admin_user(conn: sqlite3.Connection) -> int:
    """返回第一个 role='admin' 的 user id,没有则 RuntimeError。"""
    row = conn.execute(
        "SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("users 表中没有 role='admin' 用户,无法兜底 created_by")
    return row[0]


def do_migrate(db_path: str) -> None:
    """执行真正的迁移。已迁移则 no-op;半成品则 raise。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if is_already_migrated(conn):
        conn.close()
        return  # idempotent

    partial = detect_partial_state(conn)
    if partial:
        conn.close()
        raise RuntimeError(partial)

    admin_id = precheck_admin_user(conn)
    old_rows = conn.execute("SELECT * FROM api_configs ORDER BY id").fetchall()

    try:
        conn.execute("BEGIN")
        conn.execute("PRAGMA foreign_keys = OFF")

        conn.executescript(DDL_ACCOUNTS)
        conn.executescript(DDL_SUB_ACCOUNTS)
        conn.executescript(DDL_API_KEYS)

        for r in old_rows:
            mgr_username = r["manager"] or ""
            mgr_row = conn.execute(
                "SELECT id FROM users WHERE username=?", (mgr_username,)
            ).fetchone()
            mgr_id = mgr_row[0] if mgr_row else None

            now = r["updated_at"] or r["created_at"]
            cur = conn.execute(
                """INSERT INTO accounts
                   (provider, base_url, manager_user_id, created_by,
                    models, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (r["provider"], r["base_url"], mgr_id, admin_id,
                 r["models"] or "", r["is_active"], r["created_at"], now),
            )
            account_id = cur.lastrowid

            cur = conn.execute(
                """INSERT INTO sub_accounts (account_id, name, description, created_at)
                   VALUES (?, '默认', '', ?)""",
                (account_id, r["created_at"]),
            )
            sub_id = cur.lastrowid

            # 显式 id 保留(供 api_requests / usage_stats / chat_sessions 继续指向)
            conn.execute(
                """INSERT INTO api_keys
                   (id, sub_account_id, name, api_key, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (r["id"], sub_id, r["name"], r["api_key"],
                 r["is_active"], r["created_at"]),
            )

        conn.execute("DROP TABLE api_configs")
        conn.executescript(VIEW_SQL)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args(argv)
    # TODO 在后续 Task 填充
    raise NotImplementedError


if __name__ == "__main__":
    main()
