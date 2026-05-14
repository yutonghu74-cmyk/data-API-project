# Spec 3 — API 申请 & 审核重做

**日期:** 2026-05-14  
**状态:** 设计已确认，等待实施计划  
**前置:** Spec 1 + Spec 2 已落地

---

## 背景

Spec 1 建立了三层 schema（accounts → sub_accounts → api_keys），Spec 2 接通了供应商接口。但申请/审核流程仍是旧设计：用户填 `config_id`（已废弃），审核人无法按预算自动匹配 key。Spec 3 重做申请表单、审核流程、以及 claude.html / batch.html 的"已授权列表"数据源。

---

## 目标

| ID | 目标 |
|---|---|
| A1 | 申请表单简化为 5 字段：**管理员 / 供应商 / 需求预算 / 所属项目 / 项目负责人**，管理员→供应商级联 |
| A2 | 申请时记录 `account_id`（管理员+供应商唯一定位） |
| A3 | 审核弹窗显示该 account 下所有 sub_account 的未占用 key（含余额），管理员手动选 |
| A4 | 通过时 key 标占用（1:1 强约束），写入 `api_key_id` |
| B1 | `/api-requests/approved` 改写：返回 `(request_id, provider, sub_account_name, available_models)`，**不暴露 api_key 明文** |
| B2 | claude.html / batch.html 用户视图切到新数据源，调用时传 `request_id` 而非 `config_id` |
| B3 | batch.html 也走新逻辑：用户从"已审核通过的申请"中选 request_id |
| C1 | 清除 keys.html 用户视图的死代码（`quota_type / quota_amount / ip_restriction / sub_models`） |
| C2 | `/admin/users` 放开为登录即可（返 admin role 的 username + id），不再需要 X-Admin-Password |
| C3 | `/admin/api-requests` 鉴权改 X-Token，manager 只看自己 manage 的 account 的申请 |

**非范围：**
- 不动 `api_configs` 旧路径（向后兼容）
- 不做 ip_restriction / quota_amount 执行
- 不做"申请通过后撤销占用"工作流
- 不做 model subset 勾选（用 accounts.models 全量）

---

## 关键决定

| # | 决定 |
|---|---|
| 1 | **5 字段申请表单**：管理员 / 供应商 / 预算 / 项目 / 负责人；管理员→供应商级联（前端纯内存） |
| 2 | **1:1 key 占用**：一个 key 同时只能分配给一个 approved 申请；用户只能用自己被分到的 key |
| 3 | **半自动匹配**：审核弹窗列出 account 下所有 sub_account 的未占用 key + 余额，管理员手动勾选（不强制预算匹配） |
| 4 | **request_id 调用链**：`/chat`、`/script/run`、batch 都改成接 `request_id`；后端反查 api_key（旧 config_id 路径保留向后兼容） |
| 5 | **cascade 前端做**：GET `/api-requests/cascade-options` 一次拉全部 active accounts，前端纯内存级联，无新 endpoint 复杂度 |
| 6 | **manager 权限**：manager 可审核自己 manage 的 account 下的申请；platform admin 看全部 |
| 7 | **模型清单**：用 accounts.models（注册时填），不做 per-request 子集勾选 |

---

## 数据库 schema

### 新增字段

**`api_requests` 表加 3 列**（迁移走 `init_db()` 现有 try/except 模式）：

```sql
ALTER TABLE api_requests ADD COLUMN account_id   INTEGER;
ALTER TABLE api_requests ADD COLUMN api_key_id   INTEGER;
ALTER TABLE api_requests ADD COLUMN dept         TEXT DEFAULT '';
```

含义：
- `account_id`：申请定位的 account（管理员+供应商唯一确定）
- `api_key_id`：审核通过时分配的 key；1:1 约束（同一 key 最多一个 approved request）
- `dept`：部门（笔记原需求）

旧 `config_id` 列保留空着不读（向后兼容）。

---

## 后端端点

### 新增

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api-requests/cascade-options` | 返回所有 active accounts 的 `[{id, provider, manager_username, team}]`，前端纯内存级联 |
| GET | `/admin/api-requests/{req_id}/candidate-keys` | 列出 account 下所有 sub_account 的未占用 key：`[{sub_account_id, sub_account_name, api_key_id, api_key_name, last_total, last_balance}]` |

### 改写

| 方法 | 路径 | 改动 |
|------|------|------|
| POST | `/api-requests` | body 改为 `{account_id, project_name, purpose, lead, budget, dept, cc_person}`；旧 `config_id` 字段保留向后兼容但不强制 |
| GET | `/api-requests/my` | JOIN accounts，返回 `{id, account_id, provider, project_name, status, created_at}` |
| GET | `/api-requests/approved` | JOIN accounts + sub_accounts + api_keys；返回 `{request_id, provider, sub_account_name, available_models, status, created_at}`，**不返 api_key 明文** |
| PUT | `/admin/api-requests/{req_id}` | body 加 `api_key_id`；通过时检查 1:1 约束（SELECT FOR UPDATE 等价），标 key 占用 |
| GET | `/admin/api-requests` | 鉴权改 X-Token；manager 只看自己 manage 的 account 的申请；admin 看全部 |
| GET | `/admin/users` | 放开为登录即可（无需 X-Admin-Password）；返回 `[{id, username, role}]`，仅 admin role |
| POST | `/chat` | 新增 `request_id` 入参（可选）；若传则走新路径（反查 api_key），否则用旧 `config_id` 路径 |
| POST | `/script/run` | 同上 |

### 新增 helper

```python
def get_credentials_by_request(request_id: int, user_id: int):
    """反查 api_requests → api_keys.id → api_keys.api_key + accounts.base_url + accounts.provider
    校验 user_id == request.user_id and request.status == 'approved'
    返回 (api_key, base_url, provider) 或抛 403/404
    """
```

### 1:1 占用约束

审核通过时（PUT `/admin/api-requests/{req_id}`）：

```python
BEGIN IMMEDIATE;
SELECT COUNT(*) FROM api_requests 
  WHERE api_key_id = ? AND status = 'approved' AND id != ?;
# 若 > 0，抛 409 Conflict
UPDATE api_requests SET api_key_id = ?, status = 'approved', updated_at = NOW() WHERE id = ?;
COMMIT;
```

---

## 前端改造

### apply.html — 申请表单

**5 字段 + cascade：**
1. **管理员** (select)：从 `/admin/users` 拉 admin role 用户列表
2. **供应商** (select)：从 `/api-requests/cascade-options` 过滤出该管理员管理的 providers
3. **需求预算** (text)：如"10000元"
4. **所属项目** (text)：项目名称
5. **项目负责人** (text)：负责人姓名

**级联逻辑：**
- 选管理员 → 供应商下拉过滤为该管理员管理的 providers
- 选供应商 → 管理员下拉过滤为管理该供应商的 managers
- 选定两个值后，前端计算 `account_id`（笛卡尔交集，应该唯一）；若多个，弹"请进一步细分"

**提交前预览：** 显示要写入的 account_id + provider + manager

**POST body：** `{account_id, project_name, purpose, lead, budget, dept, cc_person}`

### apply.html — 审核 tab（管理员）

**审核弹窗改造：**
1. 调 `/admin/api-requests/{id}/candidate-keys` 拉 sub_account 下的未占用 key 列表
2. 表格列：`sub_account.name | api_key.name | last_total | last_balance | 选中(radio)`
3. "拒绝"按钮不变；"通过"必须选一个 key 才能点
4. 通过时 PUT `/admin/api-requests/{id}` 带 `{api_key_id, review_note}`

**列表数据：** 来自 `/admin/api-requests`（已按 manager 过滤）

### claude.html / batch.html — 用户视图

**改造 `/api-requests/approved` 调用：**
- 返回字段改为 `{request_id, provider, sub_account_name, available_models, status, created_at}`
- 去掉已死字段：`quota_type / quota_amount / ip_restriction / sub_models`

**claude.html：**
- 模型下拉直接读 `available_models`
- 调 `/chat` 时传 `request_id` 而非 `config_id`

**batch.html：**
- 改造 Step 1：从"选 config"改为"选已审核通过的申请"（下拉列表来自 `/api-requests/approved`）
- 调 `/batch/start` 时传 `request_id` 而非 `config_id`
- 后端 `/batch/start` 改接 `request_id`，反查 api_key

### keys.html — 用户视图

**清除死代码（lines 724-739）：**
- 删除对 `c.quota_type / c.quota_amount / c.ip_restriction / c.sub_models` 的读取
- 简化为：`provider | sub_account_name | available_models | 状态 | 申请时间`

---

## 鉴权迁移

- `/admin/api-requests` 系列从 X-Admin-Password 改为 X-Token（manager + admin 都能进）
- `/admin/users` 放开为登录即可（无需 X-Admin-Password）
- 旧 X-Admin-Password 的 admin.html / users.html 路径暂不动（本 spec 不做"彻底废除"）

---

## 任务粒度（精简 plan）

1. DB schema 加 3 列 + 兼容迁移
2. 后端：`cascade-options` + 改 `POST /api-requests` + 改 `GET /api-requests/my`
3. 后端：`candidate-keys` + 改写 `PUT /admin/api-requests/{id}` 含 1:1 占用约束
4. 后端：改写 `GET /api-requests/approved` + 加 `get_credentials_by_request` helper
5. 后端：`/chat`、`/script/run` 接 `request_id`；batch 改 `/batch/start`
6. 后端：`/admin/users` 放开 + `/admin/api-requests` 鉴权切 X-Token + manager 过滤
7. 前端：apply.html 申请表单 5 字段 + cascade
8. 前端：apply.html 审核弹窗 candidate-keys 表格
9. 前端：claude.html / batch.html / keys.html 用户视图切到新字段
10. 测试 + 手测 happy path

---

## 向后兼容

- `api_configs` 旧路径保留只读
- `/chat` 的 `config_id` 入参保留（若传则走旧路径）
- `api_requests.config_id` 列保留空着
