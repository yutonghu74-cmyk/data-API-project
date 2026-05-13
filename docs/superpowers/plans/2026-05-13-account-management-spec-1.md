# 帐号管理 Spec 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单层 `api_configs` 重构为三层 `accounts → sub_accounts → api_keys`,通过 VIEW 桥接老端点保零中断,新 CRUD 走 RBAC 鉴权,前端 keys.html 重做。

**Architecture:** `migrations/v1_account_schema.py` 一次性迁移老数据并替换 `api_configs` 为 VIEW;`server.py` 新增 ~15 个 `/admin/accounts*` `/admin/sub-accounts*` `/admin/api-keys*` 端点(全部 x-token + 可见权限过滤),移除老的 POST/PUT/DELETE `/admin/configs*`;`pages/keys.html` 重写管理员区为单一扁平表 + 三 modal,前端剥离硬编码 `'admin123'`。

**Tech Stack:** FastAPI / Pydantic / sqlite3,纯 ES Modules,pytest + TestClient。

参考规格: `docs/superpowers/specs/2026-05-13-account-management-spec-1-design.md`

---

## 文件映射

| 操作 | 文件 | 内容 |
|------|------|------|
| 新建 | `migrations/__init__.py` | 空文件(Python 包标志) |
| 新建 | `migrations/v1_account_schema.py` | 一次性迁移脚本 + --check / --rollback |
| 新建 | `tests/test_migration_v1.py` | 迁移脚本单元测试 |
| 修改 | `server.py` | 新增 Pydantic 模型 + auth helpers + 新端点;移除老 POST/PUT/DELETE configs;改写 fetch-models 走新表 |
| 修改 | `tests/test_admin.py` | 扩展新端点 + 权限 + 桥接 VIEW 测试 |
| 修改 | `assets/js/admin.js` | AUTH_HEADERS + 新端点封装函数 |
| 修改 | `pages/keys.html` | 重做管理员区:新表 / 列筛选 / 三 Modal,移除硬编码密码 |
| 修改 | `verify.sh` | 加新端点 smoke check |

---

## 阶段总览

| 阶段 | 任务范围 | 说明 |
|---|---|---|
| **Phase 1 (Task 1-7)** | 迁移脚本 + 真跑 | 必须先做,后面所有任务依赖新 schema |
| **Phase 2 (Task 8-21)** | 后端新端点 + TDD | schema 落地后逐个写端点 |
| **Phase 3 (Task 22-30)** | 前端 keys.html 重做 | 后端 endpoint 跑通才接前端 |
| **Phase 4 (Task 31-32)** | 验证 + 手测 | 最终冒烟 |

每个任务结束后**git commit**。

---

# Phase 1 — 迁移脚本

### Task 1: 建 `migrations/` 包骨架

**Files:**
- 新建: `migrations/__init__.py`
- 新建: `migrations/v1_account_schema.py` (空骨架)

- [ ] **Step 1: 建 migrations 目录和包标志**

```bash
mkdir -p /Users/hw-edit/Desktop/h00484736/api-web-project/migrations
touch /Users/hw-edit/Desktop/h00484736/api-web-project/migrations/__init__.py
```

- [ ] **Step 2: 写迁移脚本的 shebang 和导入**

新建 `migrations/v1_account_schema.py`,内容如下:

```python
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
```

- [ ] **Step 3: 验证脚本能执行(虽然会 NotImplementedError)**

```bash
python /Users/hw-edit/Desktop/h00484736/api-web-project/migrations/v1_account_schema.py --check
```

Expected: `NotImplementedError`(说明骨架可运行,argparse 解析对了)

- [ ] **Step 4: Commit**

```bash
git add migrations/__init__.py migrations/v1_account_schema.py
git commit -m "feat(spec1): add migration script skeleton"
```

---

### Task 2: TDD — 状态检测(is_already_migrated / detect_partial_state)

**Files:**
- 新建: `tests/test_migration_v1.py`
- 修改: `migrations/v1_account_schema.py`

- [ ] **Step 1: 写测试**

新建 `tests/test_migration_v1.py`,内容:

```python
"""迁移脚本测试。每个 case 一个独立临时 DB。"""
import os
import sqlite3
import tempfile
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from migrations import v1_account_schema as mig


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
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
pytest tests/test_migration_v1.py::TestStateDetection -v
```

Expected: 3 个 FAIL,因为 `is_already_migrated` / `detect_partial_state` 还没实现

- [ ] **Step 3: 在 `migrations/v1_account_schema.py` 里实现两个函数**

在 `VIEW_SQL = """..."""` 常量之后、`def main` 之前插入:

```python
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
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_migration_v1.py::TestStateDetection -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_migration_v1.py migrations/v1_account_schema.py
git commit -m "feat(spec1): add state detection helpers to migration"
```

---

### Task 3: TDD — 备份 + 前置检查

**Files:**
- 修改: `migrations/v1_account_schema.py`
- 修改: `tests/test_migration_v1.py`

- [ ] **Step 1: 在 tests/test_migration_v1.py 末尾加测试类**

```python
class TestBackupAndPrecheck:
    def test_backup_creates_file(self, fresh_db, tmp_path):
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
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_migration_v1.py::TestBackupAndPrecheck -v
```

Expected: 3 FAIL

- [ ] **Step 3: 实现两个函数**

在 `migrations/v1_account_schema.py` 的 `detect_partial_state` 后插入:

```python
def make_backup(db_path: str) -> str:
    """复制 DB 到 .pre-v1-{timestamp}.db,返回备份路径。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{BACKUP_PREFIX}{ts}.db"
    # 备份和原 DB 同目录
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
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_migration_v1.py::TestBackupAndPrecheck -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_migration_v1.py migrations/v1_account_schema.py
git commit -m "feat(spec1): add backup and admin precheck"
```

---

### Task 4: TDD — 核心迁移逻辑(do_migrate)

**Files:**
- 修改: `migrations/v1_account_schema.py`
- 修改: `tests/test_migration_v1.py`

- [ ] **Step 1: 在 tests/test_migration_v1.py 加 fixture 和测试**

在 `class TestBackupAndPrecheck` 之后追加:

```python
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
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_migration_v1.py::TestDoMigrate -v
```

Expected: 6 FAIL(`do_migrate` 不存在)

- [ ] **Step 3: 实现 do_migrate**

在 `migrations/v1_account_schema.py` 的 `precheck_admin_user` 之后插入:

```python
def do_migrate(db_path: str) -> None:
    """执行真正的迁移。已迁移则 no-op;半成品则 raise。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if is_already_migrated(conn):
        return  # idempotent

    partial = detect_partial_state(conn)
    if partial:
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
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_migration_v1.py::TestDoMigrate -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_migration_v1.py migrations/v1_account_schema.py
git commit -m "feat(spec1): implement core do_migrate logic"
```

---

### Task 5: TDD — 校验 + 幂等性

**Files:**
- 修改: `migrations/v1_account_schema.py`
- 修改: `tests/test_migration_v1.py`

- [ ] **Step 1: 加测试**

末尾追加:

```python
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
        for c in ("id","name","api_key","provider","base_url","models",
                  "manager","price_input","price_output","sub_account_name",
                  "is_active","created_at","updated_at"):
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
```

- [ ] **Step 2: 运行测试,确认通过(do_migrate 已支持)**

```bash
pytest tests/test_migration_v1.py::TestMigrationIdempotencyAndVerify -v
```

Expected: 4 passed (do_migrate 已实现了 is_already_migrated 早退 + raise partial)

- [ ] **Step 3: Commit**

```bash
git add tests/test_migration_v1.py
git commit -m "test(spec1): add idempotency and bridge view verification tests"
```

---

### Task 6: 实现 --check / --rollback 和命令行入口

**Files:**
- 修改: `migrations/v1_account_schema.py`
- 修改: `tests/test_migration_v1.py`

- [ ] **Step 1: 加 --check / --rollback 测试**

末尾追加:

```python
class TestCLIFlags:
    def test_check_does_not_modify_db(self, seeded_db, capsys):
        mig.main(["--db", seeded_db, "--check"])
        # 跑完后 api_configs 仍是表
        conn = sqlite3.connect(seeded_db)
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='api_configs'"
        ).fetchone()
        assert row[0] == "table"

    def test_rollback_restores_backup(self, seeded_db, tmp_path):
        backup = mig.make_backup(seeded_db)
        mig.do_migrate(seeded_db)
        # 现在 seeded_db 已迁移
        conn = sqlite3.connect(seeded_db)
        assert mig.is_already_migrated(conn)
        conn.close()
        # rollback 应该把 seeded_db 恢复
        mig.do_rollback(seeded_db)
        conn = sqlite3.connect(seeded_db)
        assert not mig.is_already_migrated(conn)
```

- [ ] **Step 2: 实现 do_check / do_rollback,改 main()**

把 `migrations/v1_account_schema.py` 的 `main` 替换为完整版,并加 do_check / do_rollback:

```python
def do_check(db_path: str) -> None:
    """干跑校验,只读,不动 DB。"""
    if not os.path.exists(db_path):
        sys.exit(f"DB 不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if is_already_migrated(conn):
        print("✓ 已迁移过")
        return
    partial = detect_partial_state(conn)
    if partial:
        sys.exit(f"❌ {partial}")
    admin_id = precheck_admin_user(conn)
    print(f"✓ 兜底 created_by = users.id {admin_id}")
    n = conn.execute("SELECT COUNT(*) FROM api_configs").fetchone()[0]
    print(f"✓ 待迁移 api_configs 行数: {n}")
    conn.close()


def do_rollback(db_path: str) -> None:
    """从最近一个 .pre-v1-*.db 备份还原。"""
    base_dir = os.path.dirname(db_path) or "."
    db_name = os.path.basename(db_path)
    candidates = [
        f for f in os.listdir(base_dir)
        if f.startswith(BACKUP_PREFIX) and f.endswith(".db")
    ]
    if not candidates:
        sys.exit("❌ 找不到备份文件")
    candidates.sort(reverse=True)
    latest = os.path.join(base_dir, candidates[0])
    shutil.copy2(latest, db_path)
    print(f"✓ 已从 {latest} 还原")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args(argv)

    if args.check:
        do_check(args.db)
        return
    if args.rollback:
        do_rollback(args.db)
        return

    # 真跑
    if not os.path.exists(args.db):
        sys.exit(f"DB 不存在: {args.db}")
    backup = make_backup(args.db)
    print(f"✓ 已备份 → {backup}")
    try:
        do_migrate(args.db)
        print("✓ 迁移成功")
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        print(f"   备份完好: {backup}")
        sys.exit(1)
```

- [ ] **Step 3: 运行所有迁移测试,确认通过**

```bash
pytest tests/test_migration_v1.py -v
```

Expected: 全绿

- [ ] **Step 4: Commit**

```bash
git add migrations/v1_account_schema.py tests/test_migration_v1.py
git commit -m "feat(spec1): add --check and --rollback CLI flags"
```

---

### Task 7: 真跑迁移(对 admin.db)

**Files:**
- 修改: `admin.db`(数据迁移,需要 git commit 进二进制变化)

- [ ] **Step 1: 干跑校验**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
python migrations/v1_account_schema.py --check
```

Expected:
```
✓ 兜底 created_by = users.id 3
✓ 待迁移 api_configs 行数: 3
```

- [ ] **Step 2: 真跑迁移**

```bash
python migrations/v1_account_schema.py
```

Expected:
```
✓ 已备份 → admin.db.pre-v1-...db
✓ 迁移成功
```

- [ ] **Step 3: 手动验证桥接 VIEW 工作正常**

```bash
sqlite3 admin.db "SELECT type FROM sqlite_master WHERE name='api_configs';"
# 期望: view

sqlite3 admin.db "SELECT id, name, api_key, provider FROM api_configs ORDER BY id;"
# 期望: 3 行,id=1,2,3 名称 testv01/02/03

sqlite3 admin.db "SELECT COUNT(*) FROM accounts;"
# 期望: 3

sqlite3 admin.db "SELECT COUNT(*) FROM sub_accounts;"
# 期望: 3(每个 account 一个 '默认')

sqlite3 admin.db "SELECT COUNT(*) FROM api_keys;"
# 期望: 3
```

- [ ] **Step 4: 验证 api_requests 仍能 JOIN**

```bash
sqlite3 admin.db "SELECT r.id, c.name FROM api_requests r JOIN api_configs c ON c.id=r.config_id;"
# 期望: 2 行,name=testv01 和 testv03
```

- [ ] **Step 5: Commit(包括 .gitignore 排除备份)**

```bash
echo "admin.db.pre-v1-*.db" >> .gitignore
git add admin.db .gitignore
git commit -m "chore(spec1): run v1 migration on admin.db"
```

---

# Phase 2 — 后端新端点

### Task 8: 添加 Pydantic 模型

**Files:**
- 修改: `server.py`

- [ ] **Step 1: 找到现有 `class ConfigIn` 位置(server.py:509)**

```bash
grep -n "class ConfigIn" /Users/hw-edit/Desktop/h00484736/api-web-project/server.py
```

期望: 一处,在 ~509 行

- [ ] **Step 2: 在 `class ConfigIn` 之前插入新模型**

在 `# ── Admin: configs ────` 这一注释之后、`class ConfigIn` 之前插入:

```python
# ── Admin: accounts (Spec 1 三层结构) ─────────────────────

class AccountIn(BaseModel):
    provider: str
    base_url: str
    provider_backend_url: str = ""
    quota_total_path: str = ""
    balance_path: str = ""
    cost_path: str = ""
    manager_user_id: int | None = None
    team: str = ""
    models: str = ""
    is_active: int = 1

class SubAccountIn(BaseModel):
    name: str
    description: str = ""

class ApiKeyIn(BaseModel):
    name: str
    api_key: str
    is_active: int = 1
    exhausted: int = 0
```

- [ ] **Step 3: 验证 server 仍能启动(语法 OK)**

```bash
python -m py_compile server.py
```

Expected: 无输出(成功)

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat(spec1): add Pydantic models for accounts/sub_accounts/api_keys"
```

---

### Task 9: 添加 auth helpers + visibility 工具函数

**Files:**
- 修改: `server.py`

- [ ] **Step 1: 找到 `get_current_user` 函数位置(server.py:669)**

```bash
grep -n "def get_current_user" /Users/hw-edit/Desktop/h00484736/api-web-project/server.py
```

- [ ] **Step 2: 在 `get_current_user` 之后插入新 helpers**

```python
def visibility_filter(user: dict) -> tuple[str, tuple]:
    """返回追加到 WHERE 的 (子句, params)。admin role 时不限制。"""
    if user["role"] == "admin":
        return "1=1", ()
    return "(a.created_by=? OR a.manager_user_id=?)", (user["id"], user["id"])


def require_owner_or_admin(user: dict, account: dict) -> None:
    """写权限:创建人或 admin role。manager 不行。"""
    if user["role"] == "admin":
        return
    if account["created_by"] == user["id"]:
        return
    raise HTTPException(status_code=403, detail="无权操作此帐号")
```

- [ ] **Step 3: 验证语法**

```bash
python -m py_compile server.py
```

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat(spec1): add visibility_filter and require_owner_or_admin"
```

---

### Task 10: TDD — GET /admin/accounts(可见性过滤)

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 找到 test_admin.py 末尾,新增测试 class**

先看一下 test_admin.py 的现有 fixture 模式:

```bash
grep -n "def client\|fixture\|TestClient" /Users/hw-edit/Desktop/h00484736/api-web-project/tests/test_admin.py | head -10
```

在文件末尾追加:

```python
# ── Spec 1 新端点测试 ────────────────────────────────────

@pytest.fixture
def db_with_users(client):
    """在 client fixture 的 DB 上插入 admin + 2 个 user。返回他们的 token。"""
    import server as srv
    with srv.get_db() as conn:
        # 清干净(防 module-scope fixture 状态污染)
        for t in ("api_keys", "sub_accounts", "accounts", "user_tokens"):
            try: conn.execute(f"DELETE FROM {t}")
            except: pass
        conn.execute("DELETE FROM users WHERE username IN ('a','b','c')")
        now = "2026-01-01"
        for uname, role in [("a","admin"), ("b","user"), ("c","user")]:
            conn.execute(
                "INSERT INTO users (username,password,role,created_at) VALUES (?,?,?,?)",
                (uname, "x", role, now),
            )
        conn.commit()
        users = {r["username"]: r["id"] for r in
                 conn.execute("SELECT id, username FROM users WHERE username IN ('a','b','c')").fetchall()}
        # 给每人一个 token
        tokens = {}
        for uname, uid in users.items():
            tok = f"tok-{uname}"
            conn.execute(
                "INSERT INTO user_tokens (user_id, token, created_at) VALUES (?,?,?)",
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
        ids = db_with_users["ids"]
        import server as srv
        # b 创建一个 account
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                         json={"provider":"p1","base_url":"http://x"})
        assert r.status_code == 200
        # a (admin) 能看到
        r = client.get("/admin/accounts", headers=auth(toks["a"]))
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_user_only_sees_own(self, client, db_with_users):
        toks = db_with_users["tokens"]
        # b 创建,c 看不到
        client.post("/admin/accounts", headers=auth(toks["b"]),
                    json={"provider":"p1","base_url":"http://x"})
        r = client.get("/admin/accounts", headers=auth(toks["c"]))
        assert r.json() == []
        r = client.get("/admin/accounts", headers=auth(toks["b"]))
        assert len(r.json()) == 1

    def test_manager_can_see(self, client, db_with_users):
        toks = db_with_users["tokens"]
        ids = db_with_users["ids"]
        # b 创建并指定 c 为 manager
        client.post("/admin/accounts", headers=auth(toks["b"]),
                    json={"provider":"p1","base_url":"http://x",
                          "manager_user_id": ids["c"]})
        r = client.get("/admin/accounts", headers=auth(toks["c"]))
        assert len(r.json()) == 1
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestGetAccounts -v
```

Expected: 4 FAIL(端点不存在)

- [ ] **Step 3: 在 server.py 实现 GET 和 POST 端点**

在 `# ── Admin: accounts` 注释这一节后插入:

```python
@app.get("/admin/accounts")
def list_accounts(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    where_sql, where_params = visibility_filter(user)
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT a.*,
                   u_mgr.username AS manager_username,
                   u_cb.username  AS creator_username
            FROM accounts a
            LEFT JOIN users u_mgr ON u_mgr.id = a.manager_user_id
            LEFT JOIN users u_cb  ON u_cb.id  = a.created_by
            WHERE {where_sql}
            ORDER BY a.id DESC
        """, where_params).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/accounts")
def create_account(body: AccountIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO accounts
              (provider, base_url, provider_backend_url, quota_total_path,
               balance_path, cost_path, manager_user_id, team, created_by,
               models, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (body.provider, body.base_url, body.provider_backend_url,
              body.quota_total_path, body.balance_path, body.cost_path,
              body.manager_user_id, body.team, user["id"], body.models,
              body.is_active, now, now))
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestGetAccounts -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): add GET/POST /admin/accounts with visibility filter"
```

---

### Task 11: TDD — PUT/DELETE /admin/accounts

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试**

在 `class TestGetAccounts` 之后:

```python
class TestUpdateDeleteAccounts:
    def test_creator_can_update(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["b"]),
                       json={"provider":"p1","base_url":"http://y","team":"研发"})
        assert r.status_code == 200
        assert r.json()["base_url"] == "http://y"
        assert r.json()["team"] == "研发"

    def test_manager_cannot_update(self, client, db_with_users):
        toks = db_with_users["tokens"]; ids = db_with_users["ids"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x",
                              "manager_user_id": ids["c"]})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["c"]),
                       json={"provider":"p2","base_url":"http://z"})
        assert r.status_code == 403

    def test_admin_can_update_anyone(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.put(f"/admin/accounts/{aid}", headers=auth(toks["a"]),
                       json={"provider":"p1","base_url":"http://y"})
        assert r.status_code == 200

    def test_delete_cascades_sub_accounts(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        client.post(f"/admin/accounts/{aid}/sub-accounts", headers=auth(toks["b"]),
                    json={"name":"sub1"})
        r = client.delete(f"/admin/accounts/{aid}", headers=auth(toks["b"]))
        assert r.status_code == 204
        # sub_accounts 应该 CASCADE 删干净
        import server as srv
        with srv.get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM sub_accounts WHERE account_id=?", (aid,)).fetchone()[0]
            assert n == 0
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestUpdateDeleteAccounts -v
```

Expected: 4 FAIL

- [ ] **Step 3: 实现 PUT 和 DELETE**

在 `create_account` 之后插入:

```python
@app.put("/admin/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn,
                   x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        acc = conn.execute(
            "SELECT * FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not acc:
            raise HTTPException(status_code=404, detail="不存在")
        require_owner_or_admin(user, acc)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE accounts SET
              provider=?, base_url=?, provider_backend_url=?, quota_total_path=?,
              balance_path=?, cost_path=?, manager_user_id=?, team=?,
              models=?, is_active=?, updated_at=?
            WHERE id=?
        """, (body.provider, body.base_url, body.provider_backend_url,
              body.quota_total_path, body.balance_path, body.cost_path,
              body.manager_user_id, body.team, body.models, body.is_active,
              now, account_id))
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row)


@app.delete("/admin/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        # 开启 FK 触发 CASCADE
        conn.execute("PRAGMA foreign_keys = ON")
        acc = conn.execute(
            "SELECT * FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not acc:
            raise HTTPException(status_code=404, detail="不存在")
        require_owner_or_admin(user, acc)
        # 检查 api_keys 是否被用过
        used = conn.execute("""
            SELECT 1 FROM usage_stats us
            JOIN api_keys k ON k.id = us.config_id
            JOIN sub_accounts s ON s.id = k.sub_account_id
            WHERE s.account_id = ?
            LIMIT 1
        """, (account_id,)).fetchone()
        if used:
            raise HTTPException(status_code=400, detail="该帐号下有 API 已被调用,不能删除")
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit()
    return  # 204
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestUpdateDeleteAccounts -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): add PUT/DELETE /admin/accounts with permission check"
```

---

### Task 12: TDD — 子帐号 CRUD

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试**

```python
class TestSubAccounts:
    def test_create_sub_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]),
                        json={"name":"S1","description":"d"})
        assert r.status_code == 200
        assert r.json()["name"] == "S1"

    def test_list_sub_accounts(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        for n in ("S1","S2"):
            client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]), json={"name":n})
        r = client.get(f"/admin/accounts/{aid}/sub-accounts",
                       headers=auth(toks["b"]))
        assert len(r.json()) == 2

    def test_update_sub_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]), json={"name":"S1"})
        sid = r.json()["id"]
        r = client.put(f"/admin/sub-accounts/{sid}", headers=auth(toks["b"]),
                       json={"name":"S1-renamed"})
        assert r.json()["name"] == "S1-renamed"

    def test_delete_sub_account_with_keys_fails(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["b"]), json={"name":"S1"})
        sid = r.json()["id"]
        client.post(f"/admin/sub-accounts/{sid}/api-keys",
                    headers=auth(toks["b"]),
                    json={"name":"k1","api_key":"sk-x"})
        r = client.delete(f"/admin/sub-accounts/{sid}", headers=auth(toks["b"]))
        assert r.status_code == 400

    def test_other_user_cannot_create_in_my_account(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p1","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["c"]), json={"name":"S1"})
        assert r.status_code == 403
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestSubAccounts -v
```

Expected: 5 FAIL

- [ ] **Step 3: 实现子帐号端点**

```python
@app.get("/admin/accounts/{account_id}/sub-accounts")
def list_sub_accounts(account_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not acc:
            raise HTTPException(404, "不存在")
        # 可见权限
        if user["role"] != "admin":
            if acc["created_by"] != user["id"] and acc["manager_user_id"] != user["id"]:
                raise HTTPException(403, "无权访问")
        rows = conn.execute(
            "SELECT * FROM sub_accounts WHERE account_id=? ORDER BY id DESC",
            (account_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/accounts/{account_id}/sub-accounts")
def create_sub_account(account_id: int, body: SubAccountIn,
                       x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not acc:
            raise HTTPException(404, "不存在")
        require_owner_or_admin(user, acc)
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO sub_accounts (account_id, name, description, created_at) "
            "VALUES (?, ?, ?, ?)",
            (account_id, body.name, body.description, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sub_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _get_sub_account_or_404(conn, sub_id: int):
    row = conn.execute("""
        SELECT s.*, a.created_by AS account_created_by,
               a.manager_user_id AS account_manager
        FROM sub_accounts s JOIN accounts a ON a.id = s.account_id
        WHERE s.id = ?
    """, (sub_id,)).fetchone()
    if not row:
        raise HTTPException(404, "子帐号不存在")
    return row


@app.put("/admin/sub-accounts/{sub_id}")
def update_sub_account(sub_id: int, body: SubAccountIn,
                       x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
        # 权限:看父帐号 created_by
        if user["role"] != "admin" and sub["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        conn.execute(
            "UPDATE sub_accounts SET name=?, description=? WHERE id=?",
            (body.name, body.description, sub_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sub_accounts WHERE id=?", (sub_id,)).fetchone()
    return dict(row)


@app.delete("/admin/sub-accounts/{sub_id}", status_code=204)
def delete_sub_account(sub_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
        if user["role"] != "admin" and sub["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        has_keys = conn.execute(
            "SELECT 1 FROM api_keys WHERE sub_account_id=? LIMIT 1", (sub_id,)
        ).fetchone()
        if has_keys:
            raise HTTPException(400, "子帐号下有 API key,不能删除")
        conn.execute("DELETE FROM sub_accounts WHERE id=?", (sub_id,))
        conn.commit()
    return
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestSubAccounts -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): add sub-accounts CRUD endpoints"
```

---

### Task 13: TDD — API key CRUD + 去重

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试**

```python
class TestApiKeys:
    def _setup_sub(self, client, toks, owner="b", provider="p1"):
        r = client.post("/admin/accounts", headers=auth(toks[owner]),
                        json={"provider":provider,"base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks[owner]), json={"name":"S"})
        return aid, r.json()["id"]

    def test_create_key(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid = self._setup_sub(client, toks)
        r = client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":"k1","api_key":"sk-aaa"})
        assert r.status_code == 200
        assert r.json()["name"] == "k1"

    def test_dedup_same_provider_same_key_rejected(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid1 = self._setup_sub(client, toks, owner="b", provider="p1")
        _, sid2 = self._setup_sub(client, toks, owner="b", provider="p1")
        client.post(f"/admin/sub-accounts/{sid1}/api-keys",
                    headers=auth(toks["b"]),
                    json={"name":"k1","api_key":"sk-same"})
        r = client.post(f"/admin/sub-accounts/{sid2}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":"k2","api_key":"sk-same"})
        assert r.status_code == 409
        assert "已存在" in r.json()["detail"]

    def test_dedup_different_provider_same_key_allowed(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid1 = self._setup_sub(client, toks, owner="b", provider="p1")
        _, sid2 = self._setup_sub(client, toks, owner="b", provider="p2")
        r1 = client.post(f"/admin/sub-accounts/{sid1}/api-keys",
                         headers=auth(toks["b"]),
                         json={"name":"k1","api_key":"sk-same"})
        r2 = client.post(f"/admin/sub-accounts/{sid2}/api-keys",
                         headers=auth(toks["b"]),
                         json={"name":"k2","api_key":"sk-same"})
        assert r1.status_code == 200 and r2.status_code == 200

    def test_list_keys(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid = self._setup_sub(client, toks)
        for n,k in [("k1","sk-1"),("k2","sk-2")]:
            client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":n,"api_key":k})
        r = client.get(f"/admin/sub-accounts/{sid}/api-keys",
                       headers=auth(toks["b"]))
        assert len(r.json()) == 2

    def test_update_key(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid = self._setup_sub(client, toks)
        r = client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":"k1","api_key":"sk"})
        kid = r.json()["id"]
        r = client.put(f"/admin/api-keys/{kid}", headers=auth(toks["b"]),
                       json={"name":"k1-new","api_key":"sk","is_active":0})
        assert r.json()["name"] == "k1-new"
        assert r.json()["is_active"] == 0

    def test_delete_unused_key(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid = self._setup_sub(client, toks)
        r = client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":"k1","api_key":"sk"})
        kid = r.json()["id"]
        r = client.delete(f"/admin/api-keys/{kid}", headers=auth(toks["b"]))
        assert r.status_code == 204

    def test_delete_used_key_rejected(self, client, db_with_users):
        toks = db_with_users["tokens"]
        _, sid = self._setup_sub(client, toks)
        r = client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["b"]),
                        json={"name":"k1","api_key":"sk"})
        kid = r.json()["id"]
        # 注入 usage_stats 记录
        import server as srv
        with srv.get_db() as conn:
            conn.execute(
                "INSERT INTO usage_stats (config_id, user_id, model, prompt_tokens, completion_tokens, cost, created_at) "
                "VALUES (?, 1, 'm', 1, 1, 0, '2026-01-01')",
                (kid,),
            )
            conn.commit()
        r = client.delete(f"/admin/api-keys/{kid}", headers=auth(toks["b"]))
        assert r.status_code == 400
        assert "调用" in r.json()["detail"]
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestApiKeys -v
```

Expected: 7 FAIL

- [ ] **Step 3: 实现 api_keys 端点**

```python
@app.get("/admin/sub-accounts/{sub_id}/api-keys")
def list_api_keys(sub_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
        if user["role"] != "admin":
            if sub["account_created_by"] != user["id"] and sub["account_manager"] != user["id"]:
                raise HTTPException(403, "无权访问")
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE sub_account_id=? ORDER BY id DESC",
            (sub_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/sub-accounts/{sub_id}/api-keys")
def create_api_key(sub_id: int, body: ApiKeyIn,
                   x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
        if user["role"] != "admin" and sub["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        # 取该 sub 对应 account 的 provider 用于去重
        provider_row = conn.execute(
            "SELECT a.provider FROM sub_accounts s "
            "JOIN accounts a ON a.id = s.account_id WHERE s.id=?", (sub_id,),
        ).fetchone()
        provider = provider_row["provider"]
        dup = conn.execute("""
            SELECT 1 FROM api_keys k
            JOIN sub_accounts s ON s.id = k.sub_account_id
            JOIN accounts a     ON a.id = s.account_id
            WHERE a.provider = ? AND k.api_key = ?
            LIMIT 1
        """, (provider, body.api_key)).fetchone()
        if dup:
            raise HTTPException(409, "该 provider 下已存在相同 key 字符串")
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO api_keys (sub_account_id, name, api_key, is_active, "
            "exhausted, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sub_id, body.name, body.api_key, body.is_active,
             body.exhausted, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _get_api_key_or_404(conn, key_id: int):
    row = conn.execute("""
        SELECT k.*, a.created_by AS account_created_by
        FROM api_keys k
        JOIN sub_accounts s ON s.id = k.sub_account_id
        JOIN accounts a     ON a.id = s.account_id
        WHERE k.id = ?
    """, (key_id,)).fetchone()
    if not row:
        raise HTTPException(404, "key 不存在")
    return row


@app.put("/admin/api-keys/{key_id}")
def update_api_key(key_id: int, body: ApiKeyIn,
                   x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        key = _get_api_key_or_404(conn, key_id)
        if user["role"] != "admin" and key["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        conn.execute(
            "UPDATE api_keys SET name=?, api_key=?, is_active=?, exhausted=? WHERE id=?",
            (body.name, body.api_key, body.is_active, body.exhausted, key_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    return dict(row)


@app.delete("/admin/api-keys/{key_id}", status_code=204)
def delete_api_key(key_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        key = _get_api_key_or_404(conn, key_id)
        if user["role"] != "admin" and key["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        used = conn.execute(
            "SELECT 1 FROM usage_stats WHERE config_id=? LIMIT 1", (key_id,)
        ).fetchone()
        if used:
            raise HTTPException(400, "已被调用,不能删除")
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()
    return
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestApiKeys -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): add api-keys CRUD with dedup and soft-delete protection"
```

---

### Task 14: TDD — providers / teams 辅助端点

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试**

```python
class TestProvidersAndTeams:
    def test_providers_distinct(self, client, db_with_users):
        toks = db_with_users["tokens"]
        for prov in ("p1","p2","p1"):
            client.post("/admin/accounts", headers=auth(toks["a"]),
                        json={"provider":prov,"base_url":"http://x"})
        r = client.get("/admin/providers", headers=auth(toks["a"]))
        assert sorted(r.json()) == ["p1", "p2"]

    def test_teams_distinct(self, client, db_with_users):
        toks = db_with_users["tokens"]
        for team in ("研发","算法","研发"):
            client.post("/admin/accounts", headers=auth(toks["a"]),
                        json={"provider":"p","base_url":"http://x","team":team})
        r = client.get("/admin/teams", headers=auth(toks["a"]))
        assert sorted(r.json()) == ["算法","研发"]

    def test_providers_respect_visibility(self, client, db_with_users):
        toks = db_with_users["tokens"]
        client.post("/admin/accounts", headers=auth(toks["b"]),
                    json={"provider":"only-b-sees","base_url":"http://x"})
        # c 看不到
        r = client.get("/admin/providers", headers=auth(toks["c"]))
        assert "only-b-sees" not in r.json()
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestProvidersAndTeams -v
```

Expected: 3 FAIL

- [ ] **Step 3: 实现端点**

```python
@app.get("/admin/providers")
def list_providers(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    where_sql, where_params = visibility_filter(user)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT a.provider FROM accounts a "
            f"WHERE {where_sql} AND a.provider <> ''",
            where_params,
        ).fetchall()
    return [r["provider"] for r in rows]


@app.get("/admin/teams")
def list_teams(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    where_sql, where_params = visibility_filter(user)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT a.team FROM accounts a "
            f"WHERE {where_sql} AND a.team <> ''",
            where_params,
        ).fetchall()
    return [r["team"] for r in rows]
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestProvidersAndTeams -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): add /admin/providers and /admin/teams distinct endpoints"
```

---

### Task 15: 重写 `/admin/configs/{id}/fetch-models` 适应新表 + 新增 `/admin/accounts/{id}/fetch-models`

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试**

```python
class TestFetchModels:
    def test_account_fetch_models_no_key_400(self, client, db_with_users):
        toks = db_with_users["tokens"]
        r = client.post("/admin/accounts", headers=auth(toks["b"]),
                        json={"provider":"p","base_url":"http://nope.invalid"})
        aid = r.json()["id"]
        r = client.get(f"/admin/accounts/{aid}/fetch-models", headers=auth(toks["b"]))
        assert r.status_code == 400
        assert "没有可用 key" in r.json()["detail"]
```

(真实供应商调用不便测,只测早退分支。Spec 2 会加完整集成测试。)

- [ ] **Step 2: 运行测试,确认失败**

```bash
pytest tests/test_admin.py::TestFetchModels -v
```

Expected: 1 FAIL

- [ ] **Step 3: 改写 server.py 中现有 fetch_models_from_provider(server.py:319-356)**

把整个函数替换为(注意端点路径保留兼容):

```python
def _fetch_models_for_key(api_key: str, base_url: str):
    """共用逻辑:用一个 key 调供应商 /v1/models。返回列表或 raise。"""
    import httpx
    base_url = (base_url or "").rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    try:
        resp = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = []
        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
        elif isinstance(data, dict) and "models" in data:
            raw = data["models"]
            models = [m["id"] if isinstance(m, dict) else str(m) for m in raw]
        elif isinstance(data, list):
            models = [m["id"] if isinstance(m, dict) else str(m) for m in data]
        return sorted(models)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"无法连接供应商:{e}")


@app.get("/admin/configs/{config_id}/fetch-models")
def fetch_models_legacy(config_id: int, x_admin_password: str = Header(default="")):
    """旧路径,config_id 现等于 api_keys.id。"""
    require_admin(x_admin_password)
    with get_db() as conn:
        row = conn.execute(
            "SELECT k.api_key, a.base_url "
            "FROM api_keys k JOIN sub_accounts s ON s.id=k.sub_account_id "
            "JOIN accounts a ON a.id=s.account_id WHERE k.id=?",
            (config_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "配置不存在")
    models = _fetch_models_for_key(row["api_key"], row["base_url"])
    return {"models": models, "empty": not models}


@app.get("/admin/accounts/{account_id}/fetch-models")
def fetch_models_for_account(account_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not acc:
            raise HTTPException(404, "帐号不存在")
        if user["role"] != "admin" and acc["created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")
        key_row = conn.execute("""
            SELECT k.api_key FROM api_keys k
            JOIN sub_accounts s ON s.id = k.sub_account_id
            WHERE s.account_id = ? AND k.is_active = 1
            ORDER BY k.id DESC LIMIT 1
        """, (account_id,)).fetchone()
    if not key_row:
        raise HTTPException(400, "该帐号下没有可用 key,无法拉取模型")
    models = _fetch_models_for_key(key_row["api_key"], acc["base_url"])
    return {"models": models, "empty": not models}
```

- [ ] **Step 4: 运行测试,确认通过**

```bash
pytest tests/test_admin.py::TestFetchModels -v
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "feat(spec1): refactor fetch-models for new schema, add account-level endpoint"
```

---

### Task 16: 移除旧写端点 (POST/PUT/DELETE `/admin/configs`)

**Files:**
- 修改: `server.py`
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 在 test_admin.py 调整老测试 — 老的 POST/PUT/DELETE 应该 404 或 410**

找到 `class TestCreateConfig`(server.py 现有写端点测试),整体替换或删除。如果当前里面有 `test_correct_auth_creates_config`,改为期望该路由不存在:

```python
class TestRemovedLegacyWriteEndpoints:
    def test_post_admin_configs_removed(self, client):
        r = client.post("/admin/configs", headers=GOOD, json={
            "name":"x","base_url":"u","api_key":"k","provider":"p"})
        assert r.status_code == 405 or r.status_code == 404
```

- [ ] **Step 2: 从 server.py 移除老的写端点函数**

删除以下函数(它们的 `@app.post` `@app.put` `@app.delete` 装饰器和函数体一起删):
- `create_config`(server.py:536-549,POST /admin/configs)
- `update_config`(server.py:551-572,PUT /admin/configs/{id})
- `delete_config`(server.py:574-584,DELETE /admin/configs/{id})

同时移除旧的子帐号端点(它们工作在错误的 schema 上,已无意义):
- 移除 `@app.get("/admin/configs/{config_id}/sub-accounts")`(server.py:985-992)
- 移除 `@app.post("/admin/configs/{config_id}/sub-accounts")`(server.py:994-1005)
- 移除 `@app.put("/admin/sub-accounts/{sub_id}")` 旧版(server.py:1007-1017) — 注意我们 Task 12 加了**同名**新端点;请确认 Task 12 的实现仍在,只移除旧的、错误 schema 的那个
- 移除 `@app.delete("/admin/sub-accounts/{sub_id}")` 旧版(server.py:1019-1025) — 同上

**重要:** 用 `grep -n "@app\.\(get\|post\|put\|delete\)" server.py` 全局看一遍重复路径,确认只有 Task 12 / Task 13 的版本留下。

- [ ] **Step 3: 同步 server.py 中老 SubAccountIn 已经存在的话 — 不存在,跳过**

```bash
grep -n "class SubAccountIn" /Users/hw-edit/Desktop/h00484736/api-web-project/server.py
```

如果有两个,删旧的那个,保留 Task 8 加的版本。

- [ ] **Step 4: 运行所有测试,确认通过**

```bash
pytest tests/test_admin.py -v
pytest tests/test_migration_v1.py -v
```

Expected: 全部 passed(包括 `TestRemovedLegacyWriteEndpoints`)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_admin.py
git commit -m "refactor(spec1): remove legacy POST/PUT/DELETE /admin/configs endpoints"
```

---

### Task 17: 桥接 VIEW 后向兼容测试

**Files:**
- 修改: `tests/test_admin.py`

- [ ] **Step 1: 加测试 — 验证桥接 VIEW 让老 SELECT 路径仍可用**

```python
class TestBridgeViewCompat:
    def test_admin_configs_select_via_view(self, client, db_with_users):
        """通过新端点创建 account+sub+key,旧 GET /admin/configs 也能看到。"""
        toks = db_with_users["tokens"]
        # 用 admin token 创建一套
        r = client.post("/admin/accounts", headers=auth(toks["a"]),
                        json={"provider":"测试","base_url":"http://x"})
        aid = r.json()["id"]
        r = client.post(f"/admin/accounts/{aid}/sub-accounts",
                        headers=auth(toks["a"]), json={"name":"默认"})
        sid = r.json()["id"]
        r = client.post(f"/admin/sub-accounts/{sid}/api-keys",
                        headers=auth(toks["a"]),
                        json={"name":"k1","api_key":"sk-via-view"})
        kid = r.json()["id"]

        # 旧端点(x-admin-password)能拿到
        r = client.get("/admin/configs", headers=GOOD)
        assert r.status_code == 200
        names = {c["name"] for c in r.json()}
        assert "k1" in names

        # /configs/{id}/models 不需要 auth,id = api_key.id 仍指向新数据
        # (models 列从 account.models 来,这里是空)
        r = client.get(f"/configs/{kid}/models")
        assert r.status_code == 200
```

- [ ] **Step 2: 运行测试,确认通过(VIEW 已在 Phase 1 建好)**

```bash
pytest tests/test_admin.py::TestBridgeViewCompat -v
```

Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_admin.py
git commit -m "test(spec1): verify bridge VIEW keeps legacy GET endpoints working"
```

---

# Phase 3 — 前端 keys.html 重做

### Task 18: 更新 `assets/js/admin.js` — AUTH_HEADERS + 新端点封装

**Files:**
- 修改: `assets/js/admin.js`

- [ ] **Step 1: 整体替换 assets/js/admin.js**

文件不大,直接覆盖。先 `cat` 一眼当前内容确认无遗漏,然后写入:

```javascript
const BASE = 'http://localhost:8000';

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Token': localStorage.getItem('token') || '',
  };
}

// 旧端点兼容:在过渡期同时携带 admin password header(后端两个都接受)
function legacyAdminHeaders() {
  const h = authHeaders();
  h['X-Admin-Password'] = sessionStorage.getItem('adminPwd') || '';
  return h;
}

async function apiFetch(path, { method = 'GET', body, headers } = {}) {
  const opts = { method, headers: headers || authHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) throw new Error('UNAUTHORIZED');
  if (res.status === 204) return null;
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch {}
    throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`);
  }
  return res.json();
}

// 旧:admin 密码登录(过渡期保留)
export async function login(password) {
  const res = await fetch(`${BASE}/admin/login`, {
    method: 'POST',
    headers: { 'X-Admin-Password': password },
  });
  const data = await res.json();
  if (data.ok) sessionStorage.setItem('adminPwd', password);
  return data.ok;
}

// 新:三层 CRUD
export const listAccounts    = ()          => apiFetch('/admin/accounts');
export const createAccount   = (body)      => apiFetch('/admin/accounts', { method:'POST', body });
export const updateAccount   = (id, body)  => apiFetch(`/admin/accounts/${id}`, { method:'PUT', body });
export const deleteAccount   = (id)        => apiFetch(`/admin/accounts/${id}`, { method:'DELETE' });

export const listSubAccounts   = (accId)        => apiFetch(`/admin/accounts/${accId}/sub-accounts`);
export const createSubAccount  = (accId, body)  => apiFetch(`/admin/accounts/${accId}/sub-accounts`, { method:'POST', body });
export const updateSubAccount  = (id, body)     => apiFetch(`/admin/sub-accounts/${id}`, { method:'PUT', body });
export const deleteSubAccount  = (id)           => apiFetch(`/admin/sub-accounts/${id}`, { method:'DELETE' });

export const listApiKeys    = (subId)        => apiFetch(`/admin/sub-accounts/${subId}/api-keys`);
export const createApiKey   = (subId, body)  => apiFetch(`/admin/sub-accounts/${subId}/api-keys`, { method:'POST', body });
export const updateApiKey   = (id, body)     => apiFetch(`/admin/api-keys/${id}`, { method:'PUT', body });
export const deleteApiKey   = (id)           => apiFetch(`/admin/api-keys/${id}`, { method:'DELETE' });

export const listProviders     = ()    => apiFetch('/admin/providers');
export const listTeams         = ()    => apiFetch('/admin/teams');
export const fetchModelsForAcc = (id)  => apiFetch(`/admin/accounts/${id}/fetch-models`);

// 老端点保留供 user 视图(GET only)
export const getConfigs    = ()  => apiFetch('/admin/configs', { headers: legacyAdminHeaders() });
export const getStats      = ()  => apiFetch('/admin/stats', { headers: legacyAdminHeaders() });
export const getDailyStats = (id) => apiFetch(`/admin/stats/${id}/daily`, { headers: legacyAdminHeaders() });
```

- [ ] **Step 2: Commit**

```bash
git add assets/js/admin.js
git commit -m "refactor(spec1): rewrite admin.js with three-tier endpoints, drop hardcoded password"
```

---

### Task 19: keys.html — 替换 adminSection HTML 结构

**Files:**
- 修改: `pages/keys.html`

- [ ] **Step 1: 找到 `<div id="adminSection"` 的起止位置**

```bash
grep -n 'id="adminSection"\|id="subAccountPanel"\|id="userSection"\|id="modalBg"\|id="subModalBg"' /Users/hw-edit/Desktop/h00484736/api-web-project/pages/keys.html
```

记录起止行号(用于精确替换)。

- [ ] **Step 2: 替换 `adminSection` 全部内容 + `subAccountPanel`(后者删除)**

把 `<div id="adminSection">...</div>` 整段(包括嵌套 toolbar、table)和紧随的 `<div id="subAccountPanel">...</div>` 整段一起删,替换为:

```html
<!-- 管理员:三层结构表 -->
<div id="adminSection" style="display:none">
  <div class="toolbar" style="margin-bottom:12px">
    <span class="page-title" style="margin:0">密钥管理</span>
    <div style="display:flex;gap:8px;margin-left:auto">
      <button class="btn btn-primary" id="addAccountBtn">+ 新增帐号</button>
      <button class="btn btn-primary" id="addKeyBtn">+ 新增 API key</button>
      <button class="btn btn-secondary" id="reloadBtn">刷新</button>
    </div>
  </div>
  <div class="card" style="padding:0;overflow:auto">
    <table id="acctTable" style="min-width:1800px">
      <thead>
        <tr>
          <th>供应商</th><th>base_url</th><th>后端网址</th>
          <th>总额接口</th><th>余额接口</th><th>费用接口</th>
          <th>子帐号</th><th>API名称</th><th>API key</th>
          <th>管理员</th><th>团队</th><th>创建人</th>
          <th>总额</th><th>余额</th><th>操作</th>
        </tr>
        <tr id="filterRow">
          <th><select data-filter="provider"><option value="">全部</option></select></th>
          <th><input data-filter="base_url" placeholder="筛选"></th>
          <th><input data-filter="backend"></th>
          <th><input data-filter="quota_total"></th>
          <th><input data-filter="balance"></th>
          <th><input data-filter="cost"></th>
          <th><select data-filter="sub_account"><option value="">全部</option></select></th>
          <th><input data-filter="api_name"></th>
          <th><input data-filter="api_key"></th>
          <th><select data-filter="manager"><option value="">全部</option></select></th>
          <th><select data-filter="team"><option value="">全部</option></select></th>
          <th><select data-filter="creator"><option value="">全部</option></select></th>
          <th>—</th><th>—</th><th>—</th>
        </tr>
      </thead>
      <tbody id="acctTbody"></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 3: 找到旧 modal 块,先全删,占位**

把 `<!-- 子账号新增/编辑弹窗 -->` 一直到 `<!-- 新增/编辑弹窗（管理员） -->` 之后的整个 modal block 全删,占位:

```html
<!-- Modals: 由 Task 20/21/22 分别加入 -->
```

(精确位置:之前 grep 出的 `subModalBg`、`modalBg` 整段)

- [ ] **Step 4: 验证 HTML 仍能用浏览器打开(无 console error)**

```bash
# 在浏览器打开 keys.html;预期:页面骨架显示,JS 报错"adminTbody 未定义"
# 这是预期的,Task 22 后会修
```

- [ ] **Step 5: Commit**

```bash
git add pages/keys.html
git commit -m "refactor(spec1): replace adminSection HTML with new three-tier table"
```

---

### Task 20: keys.html — Modal A(新增/编辑帐号)

**Files:**
- 修改: `pages/keys.html`

- [ ] **Step 1: 在 Task 19 留下的占位处插入 Modal A**

```html
<!-- Modal A: 新增/编辑帐号 -->
<div class="modal-bg" id="acctModalBg">
  <div class="modal" style="width:560px">
    <h3 id="acctModalTitle">新增帐号</h3>
    <div class="form-group"><label>供应商</label>
      <input id="aProvider" list="providerList" placeholder="如 anthropic">
      <datalist id="providerList"></datalist>
    </div>
    <div class="form-group"><label>Base URL</label>
      <input id="aBaseUrl" placeholder="https://api.anthropic.com/v1"></div>
    <div class="form-group"><label>供应商后端网址</label>
      <input id="aBackend" placeholder="Spec 2 用,可留空"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
      <div class="form-group" style="margin:0"><label>总额接口</label>
        <input id="aQuotaPath" placeholder="/v1/..."></div>
      <div class="form-group" style="margin:0"><label>余额接口</label>
        <input id="aBalancePath" placeholder="/v1/..."></div>
      <div class="form-group" style="margin:0"><label>费用接口</label>
        <input id="aCostPath" placeholder="/v1/..."></div>
    </div>
    <div class="form-group"><label>管理员</label>
      <select id="aManager"><option value="">不指定</option></select></div>
    <div class="form-group"><label>所属团队</label>
      <input id="aTeam" list="teamList">
      <datalist id="teamList"></datalist>
    </div>
    <p id="aErr" style="color:#dc2626;font-size:.83rem;min-height:18px"></p>
    <div class="modal-actions">
      <button class="btn btn-secondary" id="acctCancelBtn">取消</button>
      <button class="btn btn-primary" id="acctSaveBtn">保存</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 在 keys.html 的 `<script type="module">` 块加 Modal A 处理逻辑**

找到现有的 `const isAdmin = user.role === 'admin';` 这行,**删除以下行(硬编码密码)**:

```js
const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Admin-Password': 'admin123' };
```

然后在文件 `<script>` 块的相应位置(替换原 imports 后),把整段 admin 逻辑替换为新逻辑:

```javascript
import {
  listAccounts, createAccount, updateAccount, deleteAccount,
  listSubAccounts, createSubAccount, updateSubAccount, deleteSubAccount,
  listApiKeys, createApiKey, updateApiKey, deleteApiKey,
  listProviders, listTeams, fetchModelsForAcc,
} from '../assets/js/admin.js';

// 当前用户(已存在的 user 对象,前面代码加载来的)
const isAdmin = user.role === 'admin';

let allAccounts = [];   // [{id, provider, base_url, ..., manager_username, creator_username}]
let allSubAccounts = {}; // { account_id: [sub, ...] }
let allKeys = {};       // { sub_account_id: [key, ...] }
let adminUsers = [];    // [{id, username}] (role=admin 的 user 列表,Modal A manager dropdown 用)

// flat row 缓存:每行 = 1 个 key 的展开视图
let flatRows = [];

async function loadAll() {
  allAccounts = await listAccounts();
  allSubAccounts = {};
  allKeys = {};
  for (const a of allAccounts) {
    const subs = await listSubAccounts(a.id);
    allSubAccounts[a.id] = subs;
    for (const s of subs) {
      allKeys[s.id] = await listApiKeys(s.id);
    }
  }
  buildFlatRows();
  renderTable();
  await refreshFilterDropdowns();
}

function buildFlatRows() {
  flatRows = [];
  for (const a of allAccounts) {
    const subs = allSubAccounts[a.id] || [];
    if (!subs.length) {
      flatRows.push({ account: a, sub: null, key: null });
      continue;
    }
    for (const s of subs) {
      const keys = allKeys[s.id] || [];
      if (!keys.length) {
        flatRows.push({ account: a, sub: s, key: null });
        continue;
      }
      for (const k of keys) {
        flatRows.push({ account: a, sub: s, key: k });
      }
    }
  }
}

function renderTable() {
  const tbody = document.getElementById('acctTbody');
  const visible = applyFilters(flatRows);
  if (!visible.length) {
    tbody.innerHTML = '<tr><td colspan="15" style="text-align:center;color:var(--text-muted);padding:24px">无数据</td></tr>';
    return;
  }
  tbody.innerHTML = visible.map(r => {
    const a = r.account, s = r.sub, k = r.key;
    const keyMasked = k ? '****' + (k.api_key || '').slice(-4) : '—';
    const canEdit = isAdmin || a.created_by === user.id;
    return `<tr>
      <td>${esc(a.provider)}</td>
      <td>${esc(a.base_url)}</td>
      <td>${esc(a.provider_backend_url || '')}</td>
      <td>${esc(a.quota_total_path || '')}</td>
      <td>${esc(a.balance_path || '')}</td>
      <td>${esc(a.cost_path || '')}</td>
      <td>${s ? esc(s.name) : '—'}</td>
      <td>${k ? esc(k.name) : '—'}</td>
      <td><code>${keyMasked}</code></td>
      <td>${esc(a.manager_username || '')}</td>
      <td>${esc(a.team || '')}</td>
      <td>${esc(a.creator_username || '')}</td>
      <td style="color:var(--text-muted)">--</td>
      <td style="color:var(--text-muted)">--</td>
      <td>
        ${canEdit ? `<button class="btn-icon" data-edit-acct="${a.id}">编辑帐号</button>` : ''}
        ${k && canEdit ? `<button class="btn-icon" data-edit-key="${k.id}">编辑API</button>` : ''}
        ${k && canEdit ? `<button class="btn-icon btn-danger" data-del-key="${k.id}">删除</button>` : ''}
      </td>
    </tr>`;
  }).join('');
  // 绑定操作
  tbody.querySelectorAll('[data-edit-acct]').forEach(b =>
    b.addEventListener('click', () => openAcctEdit(parseInt(b.dataset.editAcct))));
  tbody.querySelectorAll('[data-edit-key]').forEach(b =>
    b.addEventListener('click', () => openKeyEdit(parseInt(b.dataset.editKey))));
  tbody.querySelectorAll('[data-del-key]').forEach(b =>
    b.addEventListener('click', () => onDelKey(parseInt(b.dataset.delKey))));
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Modal A 处理
const acctModalBg = document.getElementById('acctModalBg');
const acctModalTitle = document.getElementById('acctModalTitle');
const aErr = document.getElementById('aErr');
let editingAcctId = null;

async function refreshAdminUsersDropdown() {
  // 通过 /admin/users 拿 admin role users(此端点已存在)
  const res = await fetch('http://localhost:8000/admin/users', { headers: { 'X-Token': localStorage.getItem('token') || '' } });
  if (res.ok) {
    const all = await res.json();
    adminUsers = all.filter(u => u.role === 'admin');
  }
  const sel = document.getElementById('aManager');
  sel.innerHTML = '<option value="">不指定</option>' +
    adminUsers.map(u => `<option value="${u.id}">${esc(u.username)}</option>`).join('');
}

function openAcctCreate() {
  editingAcctId = null;
  acctModalTitle.textContent = '新增帐号';
  ['aProvider','aBaseUrl','aBackend','aQuotaPath','aBalancePath','aCostPath','aTeam']
    .forEach(id => document.getElementById(id).value = '');
  document.getElementById('aManager').value = '';
  aErr.textContent = '';
  refreshAdminUsersDropdown();
  acctModalBg.classList.add('open');
}

function openAcctEdit(id) {
  const a = allAccounts.find(x => x.id === id);
  if (!a) return;
  editingAcctId = id;
  acctModalTitle.textContent = '编辑帐号';
  document.getElementById('aProvider').value = a.provider;
  document.getElementById('aBaseUrl').value = a.base_url;
  document.getElementById('aBackend').value = a.provider_backend_url || '';
  document.getElementById('aQuotaPath').value = a.quota_total_path || '';
  document.getElementById('aBalancePath').value = a.balance_path || '';
  document.getElementById('aCostPath').value = a.cost_path || '';
  document.getElementById('aTeam').value = a.team || '';
  aErr.textContent = '';
  refreshAdminUsersDropdown().then(() => {
    document.getElementById('aManager').value = a.manager_user_id || '';
  });
  acctModalBg.classList.add('open');
}

document.getElementById('addAccountBtn').addEventListener('click', openAcctCreate);
document.getElementById('acctCancelBtn').addEventListener('click',
  () => acctModalBg.classList.remove('open'));
acctModalBg.addEventListener('click', e => {
  if (e.target === acctModalBg) acctModalBg.classList.remove('open');
});

document.getElementById('acctSaveBtn').addEventListener('click', async () => {
  aErr.textContent = '';
  const body = {
    provider:             document.getElementById('aProvider').value.trim(),
    base_url:             document.getElementById('aBaseUrl').value.trim(),
    provider_backend_url: document.getElementById('aBackend').value.trim(),
    quota_total_path:     document.getElementById('aQuotaPath').value.trim(),
    balance_path:         document.getElementById('aBalancePath').value.trim(),
    cost_path:            document.getElementById('aCostPath').value.trim(),
    manager_user_id:      parseInt(document.getElementById('aManager').value) || null,
    team:                 document.getElementById('aTeam').value.trim(),
    is_active: 1,
  };
  if (!body.provider || !body.base_url) {
    aErr.textContent = '供应商和 Base URL 必填'; return;
  }
  try {
    if (editingAcctId) await updateAccount(editingAcctId, body);
    else await createAccount(body);
    acctModalBg.classList.remove('open');
    await loadAll();
  } catch (e) {
    aErr.textContent = '保存失败:' + e.message;
  }
});

// 入口
async function refreshFilterDropdowns() {
  // 占位:Task 22 实现完整 filter 联动
}
function applyFilters(rows) {
  // 占位:Task 22 实现
  return rows;
}

if (isAdmin) {
  document.getElementById('adminSection').style.display = 'block';
  loadAll();
} else {
  document.getElementById('userSection').style.display = 'block';
  // 普通用户视图(原有逻辑,Spec 1 不动)
  /* ... 保留原有 userSection 加载代码 ... */
}
```

**重要:** 上面 `if (isAdmin)` 块替换原有的 `if (isAdmin)` 块。**普通用户视图的代码不要删**,原样保留(它走桥接 VIEW 自动兼容)。

- [ ] **Step 3: 浏览器手测 — 以 admin 登录,能看到表格 + 可点 "+ 新增帐号"**

```bash
python server.py &
sleep 2
# 浏览器打开 http://localhost:5500/pages/keys.html (或 file://...)
# 以 admin 用户登录
# 期望:能看到表格(3 行,来自迁移过的数据)
# 点 "+ 新增帐号" Modal A 出现,填写保存后表格刷新出新行
```

- [ ] **Step 4: Commit**

```bash
git add pages/keys.html
git commit -m "feat(spec1): add Modal A (account create/edit) to keys.html"
```

---

### Task 21: keys.html — Modal B(子帐号) + Modal C(API key)

**Files:**
- 修改: `pages/keys.html`

- [ ] **Step 1: 在 Modal A 之后追加 Modal B 和 Modal C**

```html
<!-- Modal B: 新增/编辑子帐号 -->
<div class="modal-bg" id="subModalBg">
  <div class="modal">
    <h3 id="subModalTitle">新增子帐号</h3>
    <div class="form-group"><label>所属帐号</label>
      <select id="sAccount" disabled></select></div>
    <div class="form-group"><label>名称</label>
      <input id="sName" placeholder="如 默认 / 项目A"></div>
    <div class="form-group"><label>描述</label>
      <input id="sDesc"></div>
    <p id="sErr" style="color:#dc2626;font-size:.83rem;min-height:18px"></p>
    <div class="modal-actions">
      <button class="btn btn-secondary" id="subCancelBtn">取消</button>
      <button class="btn btn-primary" id="subSaveBtn">保存</button>
    </div>
  </div>
</div>

<!-- Modal C: 新增/编辑 API key -->
<div class="modal-bg" id="keyModalBg">
  <div class="modal">
    <h3 id="keyModalTitle">新增 API key</h3>
    <div class="form-group"><label>所属帐号</label>
      <select id="kAccount"></select></div>
    <div class="form-group">
      <label>所属子帐号 <button type="button" id="kNewSubBtn" class="btn-icon"
                                style="font-size:.75rem;margin-left:8px">+ 新建</button></label>
      <select id="kSub"></select>
    </div>
    <div class="form-group"><label>API 名称</label>
      <input id="kName" placeholder="如 主key 1"></div>
    <div class="form-group"><label>API key</label>
      <textarea id="kKey" rows="2" placeholder="sk-..."></textarea></div>
    <p id="kErr" style="color:#dc2626;font-size:.83rem;min-height:18px"></p>
    <div class="modal-actions">
      <button class="btn btn-secondary" id="keyCancelBtn">取消</button>
      <button class="btn btn-primary" id="keySaveBtn">保存</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 在 script 块追加 Modal B/C 处理**

在 Task 20 末尾的 `if (isAdmin)` 块之前(也就是入口逻辑之前)追加:

```javascript
// ── Modal B 处理 ──
const subModalBg = document.getElementById('subModalBg');
let editingSubId = null;
let subModalContext = null; // { accountId, returnToKeyModal?:bool }

function openSubCreate(accountId, returnToKeyModal=false) {
  editingSubId = null;
  subModalContext = { accountId, returnToKeyModal };
  document.getElementById('subModalTitle').textContent = '新增子帐号';
  document.getElementById('sName').value = '';
  document.getElementById('sDesc').value = '';
  const accSel = document.getElementById('sAccount');
  accSel.innerHTML = allAccounts.map(a => `<option value="${a.id}" ${a.id===accountId?'selected':''}>${esc(a.provider)} / ${esc(a.base_url)}</option>`).join('');
  document.getElementById('sErr').textContent = '';
  subModalBg.classList.add('open');
}

document.getElementById('subCancelBtn').addEventListener('click', () => subModalBg.classList.remove('open'));
subModalBg.addEventListener('click', e => {
  if (e.target === subModalBg) subModalBg.classList.remove('open');
});

document.getElementById('subSaveBtn').addEventListener('click', async () => {
  const errEl = document.getElementById('sErr');
  errEl.textContent = '';
  const accountId = parseInt(document.getElementById('sAccount').value);
  const body = {
    name: document.getElementById('sName').value.trim(),
    description: document.getElementById('sDesc').value.trim(),
  };
  if (!body.name) { errEl.textContent = '名称不能为空'; return; }
  try {
    const newSub = editingSubId
      ? await updateSubAccount(editingSubId, body)
      : await createSubAccount(accountId, body);
    subModalBg.classList.remove('open');
    await loadAll();
    // 如果是从 Modal C "+ 新建" 触发的,回到 Modal C 并选中新建的子帐号
    if (subModalContext?.returnToKeyModal) {
      keyModalBg.classList.add('open');
      // 重新填充 sub dropdown
      refreshKeyModalSubDropdown(accountId);
      document.getElementById('kSub').value = newSub.id;
    }
  } catch (e) {
    errEl.textContent = '保存失败:' + e.message;
  }
});

// ── Modal C 处理 ──
const keyModalBg = document.getElementById('keyModalBg');
let editingKeyId = null;

function refreshKeyModalSubDropdown(accountId) {
  const sub = document.getElementById('kSub');
  const subs = allSubAccounts[accountId] || [];
  sub.innerHTML = subs.length
    ? subs.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')
    : '<option value="">— 暂无,请先新建 —</option>';
}

function openKeyCreate(presetSubId=null) {
  editingKeyId = null;
  document.getElementById('keyModalTitle').textContent = '新增 API key';
  document.getElementById('kName').value = '';
  document.getElementById('kKey').value = '';
  document.getElementById('kErr').textContent = '';
  const accSel = document.getElementById('kAccount');
  accSel.innerHTML = allAccounts.map(a =>
    `<option value="${a.id}">${esc(a.provider)} / ${esc(a.base_url)}</option>`).join('');
  if (presetSubId) {
    // 找出 sub 所属 account
    for (const a of allAccounts) {
      if ((allSubAccounts[a.id]||[]).some(s => s.id === presetSubId)) {
        accSel.value = a.id;
        refreshKeyModalSubDropdown(a.id);
        document.getElementById('kSub').value = presetSubId;
        break;
      }
    }
  } else if (allAccounts.length) {
    refreshKeyModalSubDropdown(allAccounts[0].id);
  }
  keyModalBg.classList.add('open');
}

function openKeyEdit(keyId) {
  let key=null, sub=null, acc=null;
  for (const a of allAccounts) {
    for (const s of allSubAccounts[a.id] || []) {
      for (const k of allKeys[s.id] || []) {
        if (k.id === keyId) { key=k; sub=s; acc=a; }
      }
    }
  }
  if (!key) return;
  editingKeyId = keyId;
  document.getElementById('keyModalTitle').textContent = '编辑 API key';
  document.getElementById('kName').value = key.name;
  document.getElementById('kKey').value = key.api_key;
  document.getElementById('kErr').textContent = '';
  const accSel = document.getElementById('kAccount');
  accSel.innerHTML = `<option value="${acc.id}">${esc(acc.provider)} / ${esc(acc.base_url)}</option>`;
  accSel.disabled = true;
  refreshKeyModalSubDropdown(acc.id);
  document.getElementById('kSub').value = sub.id;
  document.getElementById('kSub').disabled = true;
  keyModalBg.classList.add('open');
}

document.getElementById('kAccount').addEventListener('change', e => {
  refreshKeyModalSubDropdown(parseInt(e.target.value));
});
document.getElementById('kNewSubBtn').addEventListener('click', () => {
  const accId = parseInt(document.getElementById('kAccount').value);
  if (!accId) return;
  keyModalBg.classList.remove('open');
  openSubCreate(accId, /*returnToKeyModal=*/true);
});
document.getElementById('keyCancelBtn').addEventListener('click', () => {
  keyModalBg.classList.remove('open');
  document.getElementById('kAccount').disabled = false;
  document.getElementById('kSub').disabled = false;
});

document.getElementById('keySaveBtn').addEventListener('click', async () => {
  const errEl = document.getElementById('kErr');
  errEl.textContent = '';
  const subId = parseInt(document.getElementById('kSub').value);
  const body = {
    name: document.getElementById('kName').value.trim(),
    api_key: document.getElementById('kKey').value.trim(),
    is_active: 1, exhausted: 0,
  };
  if (!subId) { errEl.textContent = '请先选择或新建子帐号'; return; }
  if (!body.name || !body.api_key) { errEl.textContent = '名称和 key 必填'; return; }
  try {
    if (editingKeyId) await updateApiKey(editingKeyId, body);
    else await createApiKey(subId, body);
    keyModalBg.classList.remove('open');
    document.getElementById('kAccount').disabled = false;
    document.getElementById('kSub').disabled = false;
    await loadAll();
  } catch (e) {
    if ((e.message || '').includes('已存在')) {
      errEl.textContent = '已存在(同 provider + 同 key)';
    } else {
      errEl.textContent = '保存失败:' + e.message;
    }
  }
});

async function onDelKey(keyId) {
  if (!confirm('确认删除此 API key?')) return;
  try {
    await deleteApiKey(keyId);
    await loadAll();
  } catch (e) {
    if ((e.message || '').includes('调用')) {
      alert('该 key 已被调用,不能删除');
    } else {
      alert('删除失败:' + e.message);
    }
  }
}

document.getElementById('addKeyBtn').addEventListener('click', () => openKeyCreate());
document.getElementById('reloadBtn').addEventListener('click', loadAll);
```

- [ ] **Step 2.5: 浏览器手测**

```
□ "+ 新增 API key" 打开 Modal C
□ Modal C 里 "+ 新建" 跳到 Modal B,保存后回到 Modal C 自动选中新建子帐号
□ 输入重复 key → toast "已存在"
□ 删除被用过的 key(如迁移过来的 testv01,可能有 usage_stats) → toast "已被调用"
```

- [ ] **Step 3: Commit**

```bash
git add pages/keys.html
git commit -m "feat(spec1): add Modal B/C and CRUD for sub-accounts and api keys"
```

---

### Task 22: keys.html — 列筛选

**Files:**
- 修改: `pages/keys.html`

- [ ] **Step 1: 实现 applyFilters 和 refreshFilterDropdowns**

替换 Task 20 留下的占位实现:

```javascript
async function refreshFilterDropdowns() {
  // dropdown 选项 = 当前可见数据的 distinct 值
  const providers = [...new Set(allAccounts.map(a => a.provider).filter(Boolean))].sort();
  const teams     = [...new Set(allAccounts.map(a => a.team).filter(Boolean))].sort();
  const managers  = [...new Set(allAccounts.map(a => a.manager_username).filter(Boolean))].sort();
  const creators  = [...new Set(allAccounts.map(a => a.creator_username).filter(Boolean))].sort();
  const subs      = [...new Set(Object.values(allSubAccounts).flat().map(s => s.name).filter(Boolean))].sort();

  const fill = (sel, opts) => {
    const el = document.querySelector(`[data-filter="${sel}"]`);
    if (!el) return;
    const cur = el.value;
    el.innerHTML = '<option value="">全部</option>' +
      opts.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join('');
    el.value = cur;
  };
  fill('provider', providers);
  fill('team',     teams);
  fill('manager',  managers);
  fill('creator',  creators);
  fill('sub_account', subs);
}

function applyFilters(rows) {
  const f = {};
  document.querySelectorAll('[data-filter]').forEach(el => {
    const v = el.value.trim();
    if (v) f[el.dataset.filter] = v;
  });
  return rows.filter(r => {
    const a = r.account, s = r.sub, k = r.key;
    if (f.provider && a.provider !== f.provider) return false;
    if (f.team && a.team !== f.team) return false;
    if (f.manager && a.manager_username !== f.manager) return false;
    if (f.creator && a.creator_username !== f.creator) return false;
    if (f.sub_account && (!s || s.name !== f.sub_account)) return false;
    if (f.base_url && !(a.base_url || '').includes(f.base_url)) return false;
    if (f.backend && !(a.provider_backend_url || '').includes(f.backend)) return false;
    if (f.quota_total && !(a.quota_total_path || '').includes(f.quota_total)) return false;
    if (f.balance && !(a.balance_path || '').includes(f.balance)) return false;
    if (f.cost && !(a.cost_path || '').includes(f.cost)) return false;
    if (f.api_name && !(k?.name || '').includes(f.api_name)) return false;
    if (f.api_key && !(k?.api_key || '').includes(f.api_key)) return false;
    return true;
  });
}

// 监听筛选行变更
document.getElementById('filterRow').addEventListener('input', () => renderTable());
document.getElementById('filterRow').addEventListener('change', () => renderTable());
```

- [ ] **Step 2: 浏览器手测**

```
□ 表格加载后,顶部筛选行的 select 选项填好(provider, team, manager, creator, sub_account)
□ 选 provider 下拉 → 表格只剩对应 provider
□ base_url 框输入 "yibu" → 表格只剩 url 含此字串的行
□ 清空筛选 → 全部行回来
```

- [ ] **Step 3: Commit**

```bash
git add pages/keys.html
git commit -m "feat(spec1): add column filters to keys.html admin table"
```

---

# Phase 4 — 验证

### Task 23: 更新 verify.sh

**Files:**
- 修改: `verify.sh`

- [ ] **Step 1: 加新端点到 smoke check**

找到现有 `for path in "/health" "/admin/configs" "/admin/users"; do` 行,改为:

```bash
for path in "/health" "/admin/configs" "/admin/users" \
            "/admin/accounts" "/admin/providers" "/admin/teams"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000$path")
    # 401 也算"存在"(没带 token),只看 404 算"路由丢失"
    if [ "$code" = "404" ]; then
        echo "❌ $path -> 404 (路由丢失)"
        exit 1
    fi
    echo "✅ $path -> $code"
done
```

- [ ] **Step 2: 跑一遍**

```bash
bash verify.sh
```

Expected: 所有 path 都不是 404

- [ ] **Step 3: Commit**

```bash
git add verify.sh
git commit -m "chore(spec1): add new endpoints to verify.sh smoke check"
```

---

### Task 24: 全量手测 + grep 'admin123' 清零检查

**Files:** (无代码改动,只验证)

- [ ] **Step 1: 全测试套件**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
pytest tests/ -v
```

Expected: 全绿

- [ ] **Step 2: grep 前端任何残留的硬编码 admin password**

```bash
grep -rn "admin123\|X-Admin-Password" assets/ pages/ 2>/dev/null
```

Expected: 只在 `assets/js/admin.js` 里通过 `sessionStorage.getItem('adminPwd')` 引用(过渡期保留给老端点),**没有任何硬编码 'admin123'**

- [ ] **Step 3: 浏览器跑 spec 文档 §测试 的 "前端手测 checklist"**

参考 `docs/superpowers/specs/2026-05-13-account-management-spec-1-design.md` 的"前端手测 checklist"节,逐项打钩。

- [ ] **Step 4: 最终 commit + 标签**

```bash
git tag spec1-complete
git log --oneline | head -25
```

Expected: 看到 Task 1-23 的所有 commit + spec1-complete 标签

---

## 完成定义

- ✅ 所有 24 个任务的 step 全部勾选完
- ✅ `pytest tests/` 全绿
- ✅ `bash verify.sh` 通过
- ✅ 前端 grep 不到 `admin123`
- ✅ keys.html 管理员能创建帐号/子帐号/API key,删除受保护,列筛选可用
- ✅ claude.html 仍能聊天(桥接 VIEW 验证)
