# 帐号管理重构 Spec 1 — 设计规格

**日期：** 2026-05-13
**状态：** 设计已确认,等待实施计划

---

## 背景

现有 `api_configs` 表把"供应商配置 + API key + 价格 + 模型列表"全塞在一行。需求方要求重构为三层 `帐号 → 子帐号 → API`,并加入团队、创建人、可见权限、供应商接口配置等概念。

本 spec 仅覆盖 Spec 1(基础重构);整套重构拆为 3 个 spec 串行:

```
Spec 1: 帐号管理基础重构 (本文档)
   └─ 单独可上线:新结构 CRUD + 旧 UI 桥接兼容

Spec 2: 供应商集成 + Excel 导入  (待写)
   └─ provider_templates、余额接口、解析器 registry、Excel 导入

Spec 3: 申请/审核 + 用户端  (待写)
   └─ cascade 申请表单、自动分配 key、hide-key 字符串
```

Spec 2 依赖 Spec 1 schema 已落地;Spec 3 依赖 Spec 2 的余额接口才能做"严格等于"匹配。

---

## 目标(本 spec 范围)

| ID | 目标 |
|---|---|
| A1 | 新建 `accounts` / `sub_accounts` / `api_keys` 三表 |
| A2 | 老 `api_configs` 数据迁到新结构 |
| B1 | 帐号 CRUD,带 `created_by` / `manager_user_id` / `team`,可见权限 RBAC |
| B2 | 子帐号 CRUD(纯分组,只 name + description) |
| B3 | API key CRUD,去重(同 provider + 同 key 字符串)+ 软删保护(usage_stats 有记录则拒删) |
| B4 | `keys.html` 表格:每行 = 1 个 api_key,列筛选(文字 + 下拉),总额/余额列灰色占位 |
| B5 | 移除 UI 上的 `price_input` / `price_output` / 可用模型输入框 |
| 鉴权 | 新端点改用 `x-token` + `users.role`,前端剥离硬编码 `'admin123'` |

**非范围:** Excel 导入、供应商接口实调、申请流程改造、claude.html/batch.html 改造。这些是 Spec 2/3 的事。

---

## 架构 — 桥接 VIEW 兼容老端点

```
真实数据(新结构)              旧 API 的兼容层
─────────────────             ────────────────
accounts          (新表)
sub_accounts      (重建)    ─→  api_configs (VIEW,每行 = 1 个 api_key)
api_keys          (新表)
                              api_requests.config_id      →  api_keys.id (语义不变)
                              usage_stats.config_id        →  api_keys.id
                              chat_sessions.config_id      →  api_keys.id
                              batch_jobs.config_id         →  api_keys.id
```

**为什么用 VIEW:** claude.html / batch.html / stats.html / apply.html / `/chat` / `/configs/{id}/models` / `/api-requests/approved` 都在 SELECT `api_configs`。让 `api_configs` 变成 VIEW,这些代码 0 改动继续工作,推迟到 Spec 3 一起改完。

---

## 数据库 schema

### 新表 DDL

```sql
CREATE TABLE accounts (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  provider             TEXT NOT NULL,
  base_url             TEXT NOT NULL,
  provider_backend_url TEXT DEFAULT '',  -- Spec 2 用
  quota_total_path     TEXT DEFAULT '',  -- Spec 2 用
  balance_path         TEXT DEFAULT '',  -- Spec 2 用
  cost_path            TEXT DEFAULT '',  -- Spec 2 用
  manager_user_id      INTEGER REFERENCES users(id),
  team                 TEXT DEFAULT '',
  created_by           INTEGER NOT NULL REFERENCES users(id),
  models               TEXT DEFAULT '',  -- 保留;UI 不展示,/configs/{id}/models 仍读它
  is_active            INTEGER DEFAULT 1,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);
CREATE INDEX idx_accounts_created_by  ON accounts(created_by);
CREATE INDEX idx_accounts_manager     ON accounts(manager_user_id);
CREATE INDEX idx_accounts_team        ON accounts(team);

-- 旧 sub_accounts 当前 0 行,直接重建
DROP TABLE sub_accounts;
CREATE TABLE sub_accounts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_sub_account ON sub_accounts(account_id);

CREATE TABLE api_keys (
  id              INTEGER PRIMARY KEY,   -- 不用 AUTOINCREMENT,迁移时要显式赋 id
  sub_account_id  INTEGER NOT NULL REFERENCES sub_accounts(id) ON DELETE RESTRICT,
  name            TEXT NOT NULL,
  api_key         TEXT NOT NULL,
  is_active       INTEGER DEFAULT 1,
  exhausted       INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL
);
CREATE INDEX idx_apikeys_sub ON api_keys(sub_account_id);
```

### 桥接 VIEW

```sql
DROP TABLE api_configs;
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
```

### 约束 / 边界

| 主题 | 处理 |
|---|---|
| 去重 | 应用层 INSERT api_keys 前 SELECT 1 检查 (provider, api_key) 唯一性。冲突 → HTTP 409 |
| 删除保护 | DELETE api_keys 前 SELECT 1 FROM usage_stats WHERE config_id = api_key.id。有记录 → HTTP 400 |
| manager_user_id NULL | 合法。VIEW 用 COALESCE 给空字符串 |
| VIEW 只读 | INSERT/UPDATE/DELETE 不走 VIEW;新端点直接操作新表 |

---

## API 端点

### 新端点(全部 `x-token` 鉴权,可见权限 SQL 过滤)

```
帐号
  GET    /admin/accounts                       列表
  POST   /admin/accounts                       创建(created_by 自动填当前 user.id)
  PUT    /admin/accounts/{id}                  更新
  DELETE /admin/accounts/{id}                  删除(api_keys 被使用过 → 400)

子帐号
  GET    /admin/accounts/{id}/sub-accounts
  POST   /admin/accounts/{id}/sub-accounts
  PUT    /admin/sub-accounts/{id}
  DELETE /admin/sub-accounts/{id}              下面有 api_keys → 400 (RESTRICT)

API key
  GET    /admin/sub-accounts/{id}/api-keys
  POST   /admin/sub-accounts/{id}/api-keys     去重检查 → 409
  PUT    /admin/api-keys/{id}                  改 name / is_active / exhausted
  DELETE /admin/api-keys/{id}                  软删保护 → 400

辅助
  GET    /admin/providers                      distinct provider(可见范围内)
  GET    /admin/teams                          distinct team
  GET    /admin/accounts/{id}/fetch-models     从供应商拉模型(用账号下任意 active key)
```

### 旧端点处置

| 旧端点 | Spec 1 |
|---|---|
| `GET /admin/configs` | 保留,走 VIEW。同时支持 `x-token` 和 `x-admin-password` |
| `POST /admin/configs` | 移除 |
| `PUT /admin/configs/{id}` | 移除 |
| `DELETE /admin/configs/{id}` | 移除 |
| `GET /admin/configs/{id}/fetch-models` | 保留,内部改写。`{id}` 现 = api_keys.id |
| `GET /admin/configs/{id}/sub-accounts` 及兄弟 | 移除 |

### 鉴权 / 权限

```python
# 所有新端点
def get_admin_user(x_token: str = Header(default="")) -> dict:
    return get_current_user(x_token)  # 任何登录用户都能进

def visibility_filter(user) -> tuple[str, tuple]:
    if user["role"] == "admin":
        return "1=1", ()
    return "(a.created_by=? OR a.manager_user_id=?)", (user["id"], user["id"])

def require_owner_or_admin(user, account):
    if user["role"] == "admin": return
    if account["created_by"] == user["id"]: return
    raise HTTPException(403, "无权操作此帐号")  # manager 不能写
```

权限矩阵:

| 角色 | 看到 | 写 |
|---|---|---|
| `role=admin` | 全部 | 全部 |
| `role=user`,某帐号 `created_by` | 自己创建的 | 可改 / 可删自己创建的 |
| `role=user`,某帐号 `manager_user_id` | 该帐号 | 只读 |
| 其他 | 空 | 无 |

---

## UI(keys.html 重做)

### 总体布局

```
┌─ 顶部工具栏 ───────────────────────────────────────────┐
│ 密钥管理   [+ 新增帐号]  [+ 新增 API key]   [刷新]    │
└───────────────────────────────────────────────────────┘
┌─ 大表(每行 = 1 个 api_key,15 列) ────────────────────┐
│ 供应商|base_url|后端网址|总额接口|余额接口|费用接口   │
│        |子帐号|API名|API key|管理员|团队|创建人        │
│        |总额|余额|操作                                  │
└───────────────────────────────────────────────────────┘
```

- **筛选行(在表头):** `供应商/子帐号/管理员/团队/创建人` 用 `<select>` distinct;其余 text 列用 `<input>` substring 过滤。前端纯客户端筛。
- **API key 列**显示掩码 `****xxxx`(后 4 位)
- **总额/余额** Spec 1 一律灰 `--`(Spec 2 真调接口)
- **操作列:** 编辑帐号 / 编辑 API / 删除(权限不足时按钮置灰)

### 三个 Modal

**Modal A — 新增/编辑帐号:** 供应商、base_url、后端网址、总额接口、余额接口、费用接口、管理员(`<select>` from users where role=admin)、所属团队(text + datalist)。**不含 api_key、API 名称、可用模型、price。**

**Modal B — 新增/编辑子帐号:** 所属帐号(select,编辑时只读)、名称、描述。

**Modal C — 新增/编辑 API key:** 所属帐号、所属子帐号(级联,支持内联"+ 新建子帐号")、API 名称、API key(密码/textarea)。保存时校验去重,409 显示 toast "已存在"。**Spec 1 不含 Excel 导入按钮(Spec 2)。**

### 前端鉴权改造

```diff
-const ADMIN_HEADERS = { 'Content-Type':'application/json', 'X-Admin-Password':'admin123' };
+const AUTH_HEADERS  = { 'Content-Type':'application/json',
+                         'X-Token': localStorage.getItem('token') };
```

硬编码的 `'admin123'` 从 `keys.html` 彻底删除。

### 不动的部分

- `#userSection`(普通用户已授权列表):基于 `/api-requests/approved`,通过桥接 VIEW 自动兼容,Spec 1 不动
- `pages/admin.html`(旧的简化版):Spec 1 不动,无人访问

---

## 迁移

### 脚本:`migrations/v1_account_schema.py`(新)

幂等、有备份、支持回滚。

**流程:**

1. 备份 → `admin.db.pre-v1-{timestamp}.db`
2. 检测前置:`users` 表至少有一个 `role='admin'` 用户(当前是 id=3 username='admin'),用作所有迁移行的兜底 `created_by`
3. `PRAGMA foreign_keys = OFF`(让 DROP api_configs 不报 FK 错;本项目 FK 本就不强制)
4. `BEGIN TRANSACTION`
5. CREATE 新三表
6. 遍历老 `api_configs`(预计 3 行):
   - 反解老 `manager` 字符串到 `users.id`(查不到则 NULL)
   - INSERT accounts → 拿新 account_id
   - INSERT sub_accounts(name='默认',account_id)→ 拿新 sub_account_id
   - INSERT api_keys(**id 显式赋为旧 api_configs.id**,sub_account_id, name, api_key)
7. DROP TABLE api_configs(老数据已迁出)
8. CREATE VIEW api_configs(桥接)
9. 校验:
   - 行数:`SELECT COUNT(*) FROM api_configs(view)` == 老行数
   - 每行 id / name / api_key 对得上
   - `api_requests JOIN api_configs` 没有 NULL 行
10. COMMIT

### 用法

```bash
python migrations/v1_account_schema.py --check    # 干跑校验
python migrations/v1_account_schema.py            # 真跑
python migrations/v1_account_schema.py --rollback # 从最近备份还原
```

**不接进 server 启动流程**:DB schema 变更必须显式动作,不偷偷在生产 init 里做。

### 已知数据

- `api_configs`: 3 行(testv01/02/03)
- `sub_accounts`: 0 行(老 schema 表内无数据,直接 DROP 重建)
- `api_requests`: 2 行 approved(user_id=3, config_id=1 和 3),迁移后 config_id 自动指向新 api_keys.id 1 和 3
- `users.role='admin'`: 1 个(id=3 username='admin')
- 老 `manager` 字符串值: 空、"胡宇彤"、"hytt" —— 在 users 表里都找不到对应,迁移后 `manager_user_id = NULL`

---

## 测试

### 1. 迁移脚本:`tests/test_migration_v1.py`(新)

```
- 干跑 --check 不写库
- 正常迁移 3 行 → 3 accounts/3 sub_accounts/3 api_keys,id 完全保留
- manager 找不到 user → NULL,不报错
- manager 匹配 user → 正确 FK
- 重跑幂等:VIEW 已存在 → 直接退出
- 半成品状态检测(accounts 存在但 api_configs 仍是表)→ 报错退出
- 注入 SQL 错误验证 ROLLBACK,备份完好
- 桥接 VIEW SELECT * 列名 + 数据与迁移前一致
- api_requests.config_id JOIN api_configs 仍非空
```

### 2. 新端点 + 鉴权:扩展 `tests/test_admin.py`

```
鉴权
  - 无 x-token → 401
  - 普通 user token → 看不到不属于自己的 accounts
  - admin token → 看到全部

可见权限
  - admin 可见 + 可编辑所有
  - user A 创建 X,user B(user) 看不到 X
  - user A 创建 X,manager 设 user B → B 可见但 PUT/DELETE 403
  - user A 创建 X,user C(admin) 可见 + 可改

帐号 CRUD
  - POST 自动 created_by = self
  - PUT 改 manager,旧 manager 失去可见
  - DELETE 有被用过的 api_keys → 400
  - DELETE 干净帐号 → CASCADE 删 sub_accounts

子帐号 CRUD
  - 必须在已有 account 下
  - 下面有 api_keys → 400(RESTRICT)

API key CRUD + 去重
  - 同 sub-account 下 (name, key) 创建 OK
  - 同 provider 不同 account 但 key 字符串相同 → 409
  - 同 key 字符串但不同 provider → OK
  - DELETE 没记录 → 204
  - DELETE 有 usage_stats 记录 → 400
  - PUT 改 name / is_active / exhausted

桥接 VIEW 后向兼容
  - GET /admin/configs 用 x-admin-password 仍返回扁平列表
  - GET /configs/{id}/models 用 api_key.id 仍 OK
  - /chat 拿 config_id 仍能在 api_configs 找到行
  - /api-requests/approved 仍返回老数据形态
```

### 3. 前端手测 checklist

放进 PR 描述的 `## Test Plan`:

```
□ migration --check 干跑通过
□ migration 正常跑,3 行迁好
□ 重跑显示"已迁移过"
□ keys.html 以 admin 登录:
  □ 表格 3 行,API key 列掩码,列筛选可用
  □ 总额/余额列灰 "--"
  □ "+ 新增帐号" → Modal A 填写保存 → 新行出现
  □ "+ 新增 API key" → Modal C 选父级 + 填写 → 新行出现
  □ 重复 (provider, key) → toast "已存在"
  □ 改 manager → 旧 manager 视图丢失
  □ 删除 testv01(有 usage_stats) → toast "已被调用"
  □ 删除干净 key → 行消失
□ 普通用户 keys.html: adminSection 不显示,userSection 仍有列表
□ claude.html 已审批用户发消息走通,模型下拉正常
□ batch.html / stats.html 加载正常
```

### 4. `verify.sh` 更新

加新端点检查:

```bash
for path in "/health" "/admin/configs" "/admin/users" \
            "/admin/accounts" "/admin/providers" "/admin/teams"; do ...
```

---

## 留给 Spec 2/3 的钩子

### 已埋好

| 钩子 | Spec 1 | Spec 2/3 接 |
|---|---|---|
| `accounts.{provider_backend_url, quota_total_path, balance_path, cost_path}` | 4 字段空字符串默认,Modal A 已收集 | Spec 2 真调 |
| `api_keys.exhausted` | 字段存在,Spec 1 不写不读 | Spec 2 写,Spec 3 跳过 |
| 表格"总额/余额"两列 | 灰 `--` 占位 | Spec 2 填实际值 |
| 桥接 VIEW `api_configs` | Spec 1 建 | Spec 3 完成 chat/batch 改造后 DROP VIEW |
| `/admin/providers`、`/admin/teams` | Spec 1 已返回 | Spec 3 apply cascade 直接用 |

### Spec 1 明确不做

- ❌ Excel 导入 endpoint → Spec 2
- ❌ provider_templates 表 + 自动填表 → Spec 2
- ❌ 真调供应商总额/余额/费用接口 → Spec 2
- ❌ 解析器 registry → Spec 2
- ❌ 申请表单 cascade、自动分配、推荐 key → Spec 3
- ❌ api_key_assignments 表 → Spec 3
- ❌ claude.html / batch.html 显示 API 名称、hide-key → Spec 3
- ❌ 彻底废除 `x-admin-password` → Spec 3

### Spec 2 / 3 接口形状(预先冻结)

**Spec 2 — Excel 导入:**
```
POST /admin/sub-accounts/{id}/api-keys/import-excel
multipart: file=<xlsx>(列:API名称, API key)
200 → { imported, skipped_duplicates, errors }
```

**Spec 2 — 余额读取:**
```
GET /admin/api-keys/{id}/quota
200 → { total, balance, cost, fetched_at }
502 → { detail } (前端灰)
```

**Spec 3 — 自动分配候选:**
```
POST /admin/api-requests/{id}/recommend-key
200 → { candidate: {api_key_id, name, balance} }
404 → { detail: "无余额恰好等于 X 元的未分配 key" }
```

---

## 风险 / 已知不足

| 风险 | 缓解 |
|---|---|
| VIEW 只读,如果某老端点意外 INSERT/UPDATE 到 api_configs,会 SQL 报错 | 已盘查代码,只有被移除的 `POST/PUT/DELETE /admin/configs` 三个写端点;其余全 SELECT |
| 老 `manager` 字符串数据(胡宇彤、hytt)在迁移后丢失 | 数据本来就没和 users 绑定,迁完是 NULL。如果需要保留,后续可在 accounts 加 `legacy_manager_text` 字段,但 Spec 1 不做 |
| 前端列筛选 15 列,移动端展示困难 | Spec 1 优先桌面;移动端体验放 Spec 2/3 |
| 鉴权切换:keys.html 用户必须以 admin role 用户登录(不能只靠老 admin 密码) | 当前 `users` 表已有 admin 用户,无破坏 |
| 迁移脚本不接 init 流程,部署时容易忘 | README + PR 描述强调;Spec 1 验收前提是 "迁移已成功" |

---

## 验收标准

1. `migrations/v1_account_schema.py --check` 通过
2. 真跑迁移后 `SELECT * FROM api_configs` 仍返回 3 行,字段与迁移前一致
3. `tests/test_migration_v1.py` 全绿
4. `tests/test_admin.py` 扩展用例全绿
5. claude.html 以已审批用户身份能发一条消息,SSE 流正常
6. keys.html admin 用户能创建新帐号 / 子帐号 / API key,删除受保护工作正常
7. 前端 grep 不到 `'admin123'`
