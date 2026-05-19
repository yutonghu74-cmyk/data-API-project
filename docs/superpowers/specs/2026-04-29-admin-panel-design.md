A# 管理员界面 — 设计规格

**日期：** 2026-04-29  
**状态：** 已批准

---

## 目标

为 api-web-project 新增管理员界面，支持 API 密钥配置管理、历史配置查看、密钥使用统计（调用次数、token 消耗、趋势图、响应时间）。

---

## 架构

```
pages/admin.html
    │  fetch /admin/*（X-Admin-Password header）
    ▼
server.py（扩展）
    │  新增 /admin/* 端点 + /chat 统计埋点
    ▼
admin.db（SQLite，项目根目录）
    ├── api_configs 表
    └── usage_stats 表
```

---

## 新增/修改文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `server.py` | SQLite 初始化、管理端点、/chat 统计埋点 |
| 新建 | `pages/admin.html` | 管理界面（登录+密钥管理+统计+设置） |
| 新建 | `assets/js/admin.js` | 管理页 fetch 封装 |

---

## 数据库结构（admin.db）

```sql
CREATE TABLE api_configs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  base_url   TEXT NOT NULL,
  api_key    TEXT NOT NULL,
  provider   TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  is_active  INTEGER DEFAULT 1
);

CREATE TABLE usage_stats (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  config_id     INTEGER REFERENCES api_configs(id),
  called_at     TEXT NOT NULL,
  model         TEXT,
  input_tokens  INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  success       INTEGER DEFAULT 1,
  duration_ms   INTEGER DEFAULT 0,
  error_msg     TEXT
);
```

---

## 后端设计（server.py 新增）

### 认证

所有 `/admin/*` 端点通过请求头 `X-Admin-Password` 验证，与环境变量 `ADMIN_PASSWORD` 比对（默认值 `admin123`）。不匹配返回 401。

### 端点列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/admin/login` | POST | 验证密码，返回 `{ok: true/false}` |
| `/admin/configs` | GET | 获取所有 API 配置（api_key 只返回末4位） |
| `/admin/configs` | POST | 新增配置 |
| `/admin/configs/{id}` | PUT | 更新配置 |
| `/admin/configs/{id}` | DELETE | 删除配置及其统计数据 |
| `/admin/stats` | GET | 所有密钥统计摘要（调用数、成功率、token、耗时） |
| `/admin/stats/{config_id}/daily` | GET | 指定密钥最近 7 天每日调用次数 |

### /chat 统计埋点

`ChatRequest` 新增可选字段 `config_id: int | None = None`。  
每次 `/chat` 请求完成后（流结束），若 `config_id` 不为空，向 `usage_stats` 写入一条记录：
- `called_at`：请求开始时间（ISO 格式）
- `model`：使用的模型
- `input_tokens` / `output_tokens`：从 Anthropic stream 的 `usage` 事件获取
- `success`：1=正常结束，0=异常
- `duration_ms`：从请求开始到流结束的毫秒数
- `error_msg`：失败时的错误信息

---

## 前端设计（pages/admin.html）

### 登录页

居中卡片，密码输入框 + 登录按钮。验证通过后密码存入 `sessionStorage`，进入主界面。刷新后需重新登录。

### 主界面布局

```
┌──────────┬────────────────────────────────────┐
│  🔑 密钥  │  标题栏                             │
│  📊 统计  ├────────────────────────────────────┤
│  ⚙️ 设置  │  内容区（tab 切换）                 │
└──────────┴────────────────────────────────────┘
```

### 密钥管理页（默认 tab）

- 表格：名称、提供商、密钥末4位、状态（启用/禁用）、创建时间、操作（编辑/删除）
- 「新增配置」按钮 → 弹窗（name、provider、base_url、api_key 四个字段）
- 删除时弹确认对话框

### 统计页

- 每个 api_config 一张卡片，显示：
  - 总调用次数
  - 成功率（%）
  - 累计 input + output token 数
  - 平均响应时间（ms）
  - 最后使用时间
- 点击卡片展开最近 7 天趋势折线图（SVG 绘制，无第三方库）

### 设置页

- 显示当前 `ADMIN_PASSWORD` 环境变量名
- 提示：修改密码需在 `.env` 文件中更新 `ADMIN_PASSWORD` 后重启服务

---

## 安全说明

- api_key 在数据库中明文存储（本地个人工具，不对外暴露）
- GET `/admin/configs` 返回时 api_key 只暴露末4位（`****xxxx`）
- 管理端点全部需要 `X-Admin-Password` header，浏览器 sessionStorage 不跨 tab 持久

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 密码错误 | 登录页显示"密码错误" |
| 新增配置字段为空 | 前端拦截，弹窗内显示错误提示 |
| 删除有统计记录的配置 | 级联删除 usage_stats 中对应记录 |
| /chat 统计写入失败 | 静默忽略，不影响聊天功能 |

---

## 启动方式

```bash
# .env 中新增（可选，默认 admin123）
ADMIN_PASSWORD=your-password

python server.py
# 打开 pages/admin.html
```
