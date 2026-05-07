# 管理员界面实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 api-web-project 新增管理员界面，支持 API 密钥配置管理、历史查看、使用统计（调用数、token、趋势图、响应时间）。

**Architecture:** 扩展 server.py 新增 SQLite（admin.db）初始化和 /admin/* 端点；/chat 埋点写入 usage_stats；前端 pages/admin.html + assets/js/admin.js 实现登录、密钥管理、统计三个 tab。

**Tech Stack:** Python FastAPI + sqlite3（标准库）+ Pydantic，原生 ES Modules，SVG 折线图（无第三方库）

---

## 文件映射

| 操作 | 文件 | 内容 |
|------|------|------|
| 修改 | `server.py` | SQLite 初始化、认证依赖、管理端点、/chat 统计埋点 |
| 新建 | `assets/js/admin.js` | adminFetch() 封装 + 各端点调用函数 |
| 新建 | `pages/admin.html` | 登录页 + 主界面（密钥/统计/设置三 tab）|
| 修改 | `index.html` | pages 数组新增管理员入口卡片 |

---

### Task 1: 扩展 server.py — SQLite 初始化 + 认证依赖

**Files:**
- 修改: `server.py`

- [ ] **Step 1: 在现有 import 块后插入 SQLite 相关代码**

在 `server.py` 第 13 行（`from typing import Literal`）之后插入：

```python
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import Header
from fastapi.responses import JSONResponse

DB_PATH = os.path.join(os.path.dirname(__file__), "admin.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_configs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                base_url   TEXT NOT NULL,
                api_key    TEXT NOT NULL,
                provider   TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active  INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id     INTEGER REFERENCES api_configs(id),
                called_at     TEXT NOT NULL,
                model         TEXT,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                success       INTEGER DEFAULT 1,
                duration_ms   INTEGER DEFAULT 0,
                error_msg     TEXT
            )
        """)
        conn.commit()

init_db()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def require_admin(x_admin_password: str = Header(default="")):
    if x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
```

- [ ] **Step 2: 验证语法**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "import ast; ast.parse(open('server.py').read()); print('syntax OK')"
```

预期：`syntax OK`

- [ ] **Step 3: 验证数据库初始化**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "
from server import init_db, DB_PATH
import sqlite3, os
init_db()
conn = sqlite3.connect(DB_PATH)
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
assert 'api_configs' in tables, 'api_configs missing'
assert 'usage_stats' in tables, 'usage_stats missing'
conn.close()
print('db OK')
"
```

预期：`db OK`

- [ ] **Step 4: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add server.py
git commit -m "feat: add SQLite init and admin auth dependency to server.py"
```

---

### Task 2: 扩展 server.py — 管理端点 CRUD

**Files:**
- 修改: `server.py`（在 `if __name__ == "__main__":` 之前追加）

- [ ] **Step 1: 追加管理端点代码**

在 `server.py` 末尾的 `if __name__ == "__main__":` 之前插入：

```python
# ── Admin: configs ────────────────────────────────────────

class ConfigIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    provider: str
    is_active: int = 1

@app.post("/admin/login")
def admin_login(x_admin_password: str = Header(default="")):
    return {"ok": x_admin_password == ADMIN_PASSWORD}

@app.get("/admin/configs")
def list_configs(x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM api_configs ORDER BY id DESC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["api_key"] = "****" + d["api_key"][-4:] if len(d["api_key"]) >= 4 else "****"
        result.append(d)
    return result

@app.post("/admin/configs")
def create_config(body: ConfigIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO api_configs (name,base_url,api_key,provider,created_at,updated_at,is_active) VALUES (?,?,?,?,?,?,?)",
            (body.name, body.base_url, body.api_key, body.provider, now, now, body.is_active)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_configs WHERE id=?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["api_key"] = "****" + d["api_key"][-4:]
    return d

@app.put("/admin/configs/{config_id}")
def update_config(config_id: int, body: ConfigIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE api_configs SET name=?,base_url=?,api_key=?,provider=?,updated_at=?,is_active=? WHERE id=?",
            (body.name, body.base_url, body.api_key, body.provider, now, body.is_active, config_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_configs WHERE id=?", (config_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    d = dict(row)
    d["api_key"] = "****" + d["api_key"][-4:]
    return d

@app.delete("/admin/configs/{config_id}")
def delete_config(config_id: int, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        conn.execute("DELETE FROM usage_stats WHERE config_id=?", (config_id,))
        conn.execute("DELETE FROM api_configs WHERE id=?", (config_id,))
        conn.commit()
    return {"ok": True}

# ── Admin: stats ───────────────────────────────────────────

@app.get("/admin/stats")
def list_stats(x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        configs = conn.execute("SELECT id, name, provider FROM api_configs").fetchall()
        result = []
        for cfg in configs:
            cid = cfg["id"]
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(success) as ok,
                       SUM(input_tokens+output_tokens) as tokens,
                       AVG(duration_ms) as avg_ms,
                       MAX(called_at) as last_used
                FROM usage_stats WHERE config_id=?
            """, (cid,)).fetchone()
            total = row["total"] or 0
            ok    = row["ok"] or 0
            result.append({
                "config_id":   cid,
                "name":        cfg["name"],
                "provider":    cfg["provider"],
                "total_calls": total,
                "success_rate": round(ok / total * 100, 1) if total else 0,
                "total_tokens": row["tokens"] or 0,
                "avg_duration_ms": round(row["avg_ms"] or 0, 1),
                "last_used":   row["last_used"] or "—",
            })
    return result

@app.get("/admin/stats/{config_id}/daily")
def daily_stats(config_id: int, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT substr(called_at,1,10) as day, COUNT(*) as cnt
            FROM usage_stats
            WHERE config_id=?
              AND called_at >= date('now','-6 days')
            GROUP BY day ORDER BY day
        """, (config_id,)).fetchall()
    return [{"day": r["day"], "count": r["cnt"]} for r in rows]
```

- [ ] **Step 2: 验证语法**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "import ast; ast.parse(open('server.py').read()); print('syntax OK')"
```

预期：`syntax OK`

- [ ] **Step 3: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add server.py
git commit -m "feat: add admin CRUD and stats endpoints"
```

---

### Task 3: 扩展 server.py — /chat 统计埋点

**Files:**
- 修改: `server.py`（ChatRequest 和 chat 端点）

- [ ] **Step 1: 在 ChatRequest 中新增 config_id 字段**

找到：
```python
class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "claude-sonnet-4-6"
    system: str = ""
```

替换为：
```python
class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "claude-sonnet-4-6"
    system: str = ""
    config_id: int | None = None
```

- [ ] **Step 2: 替换 chat 端点，加入统计埋点**

找到完整的 `@app.post("/chat")` 端点，替换为：

```python
@app.post("/chat")
async def chat(req: ChatRequest):
    if req.model not in MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {req.model}")

    start_time = time.time()
    called_at  = datetime.now(timezone.utc).isoformat()

    def generate():
        input_tokens  = 0
        output_tokens = 0
        success       = 1
        error_msg     = None
        try:
            kwargs = dict(
                model=req.model,
                max_tokens=4096,
                messages=[m.model_dump() for m in req.messages],
            )
            if req.system:
                kwargs["system"] = req.system

            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
                usage = stream.get_final_message().usage
                input_tokens  = usage.input_tokens
                output_tokens = usage.output_tokens
            yield "data: [DONE]\n\n"
        except Exception as e:
            success   = 0
            error_msg = str(e)
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
        finally:
            if req.config_id is not None:
                duration_ms = int((time.time() - start_time) * 1000)
                try:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO usage_stats (config_id,called_at,model,input_tokens,output_tokens,success,duration_ms,error_msg) VALUES (?,?,?,?,?,?,?,?)",
                            (req.config_id, called_at, req.model, input_tokens, output_tokens, success, duration_ms, error_msg)
                        )
                        conn.commit()
                except Exception:
                    pass

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 3: 验证语法**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "import ast; ast.parse(open('server.py').read()); print('syntax OK')"
```

预期：`syntax OK`

- [ ] **Step 4: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add server.py
git commit -m "feat: add usage stats tracking to /chat endpoint"
```

---

### Task 4: 新建 assets/js/admin.js

**Files:**
- 新建: `assets/js/admin.js`

- [ ] **Step 1: 写入 admin.js**

```js
const BASE = 'http://localhost:8000';

function getPassword() {
  return sessionStorage.getItem('adminPwd') || '';
}

async function adminFetch(path, { method = 'GET', body } = {}) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Password': getPassword(),
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) throw new Error('UNAUTHORIZED');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function login(password) {
  const res = await fetch(`${BASE}/admin/login`, {
    method: 'POST',
    headers: { 'X-Admin-Password': password },
  });
  const data = await res.json();
  if (data.ok) sessionStorage.setItem('adminPwd', password);
  return data.ok;
}

export const getConfigs     = ()         => adminFetch('/admin/configs');
export const createConfig   = (body)     => adminFetch('/admin/configs', { method: 'POST', body });
export const updateConfig   = (id, body) => adminFetch(`/admin/configs/${id}`, { method: 'PUT', body });
export const deleteConfig   = (id)       => adminFetch(`/admin/configs/${id}`, { method: 'DELETE' });
export const getStats       = ()         => adminFetch('/admin/stats');
export const getDailyStats  = (id)       => adminFetch(`/admin/stats/${id}/daily`);
```

- [ ] **Step 2: 验证文件存在**

```bash
ls -lh /Users/hw-edit/Desktop/h00484736/api-web-project/assets/js/admin.js
```

预期：文件存在，大小 > 500B

- [ ] **Step 3: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add assets/js/admin.js
git commit -m "feat: add admin.js API client"
```

---

### Task 5: 新建 pages/admin.html

**Files:**
- 新建: `pages/admin.html`

- [ ] **Step 1: 写入完整 admin.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>管理员面板</title>
  <link rel="stylesheet" href="../assets/css/base.css">
  <style>
    body { display: flex; flex-direction: column; min-height: 100vh; }

    /* 登录页 */
    .login-wrap {
      flex: 1; display: flex; align-items: center; justify-content: center;
    }
    .login-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 40px 48px; width: 340px; text-align: center;
    }
    .login-card h1 { font-size: 1.2rem; margin-bottom: 24px; }
    .login-card input { margin-bottom: 14px; }
    .login-error { color: var(--error); font-size: .85rem; margin-top: 8px; min-height: 20px; }

    /* 主界面 */
    .app { display: none; flex: 1; }
    .sidebar {
      width: 180px; background: var(--surface); border-right: 1px solid var(--border);
      padding: 24px 12px; display: flex; flex-direction: column; gap: 4px; position: fixed;
      top: 0; left: 0; bottom: 0;
    }
    .sidebar h2 { font-size: .8rem; color: var(--text-muted); text-transform: uppercase;
      letter-spacing: 1px; padding: 0 8px; margin-bottom: 8px; }
    .nav-item {
      display: flex; align-items: center; gap: 8px; padding: 10px 12px;
      border-radius: 8px; cursor: pointer; font-size: .9rem; color: var(--text-muted);
      border: none; background: none; width: 100%; text-align: left;
    }
    .nav-item:hover { background: var(--border); color: var(--text); }
    .nav-item.active { background: var(--accent); color: #fff; }
    .main { margin-left: 180px; padding: 32px 32px; flex: 1; }
    .page { display: none; }
    .page.active { display: block; }
    .page-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 24px; }

    /* 表格 */
    table { width: 100%; border-collapse: collapse; font-size: .9rem; }
    th { text-align: left; padding: 10px 14px; font-size: .8rem; color: var(--text-muted);
      border-bottom: 1px solid var(--border); }
    td { padding: 12px 14px; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .78rem;
    }
    .badge-on  { background: rgba(52,211,153,.15); color: var(--success); }
    .badge-off { background: rgba(107,114,128,.15); color: var(--text-muted); }

    /* 弹窗 */
    .modal-bg {
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5);
      align-items: center; justify-content: center; z-index: 100;
    }
    .modal-bg.open { display: flex; }
    .modal {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 28px 32px; width: 440px;
    }
    .modal h3 { font-size: 1rem; margin-bottom: 20px; }
    .form-group { margin-bottom: 14px; }
    .modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px; }

    /* 统计卡片 */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px,1fr)); gap: 16px; }
    .stat-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px; cursor: pointer;
      transition: border-color .2s;
    }
    .stat-card:hover { border-color: var(--accent); }
    .stat-card h3 { font-size: .95rem; font-weight: 600; margin-bottom: 12px; }
    .stat-row { display: flex; justify-content: space-between; font-size: .85rem;
      color: var(--text-muted); margin-bottom: 6px; }
    .stat-row span:last-child { color: var(--text); font-weight: 500; }
    .chart-wrap { margin-top: 14px; display: none; }
    .chart-wrap.open { display: block; }

    /* SVG 折线图 */
    .chart-svg { width: 100%; height: 80px; }
    .chart-label { display: flex; justify-content: space-between;
      font-size: .7rem; color: var(--text-muted); margin-top: 4px; }

    /* 操作按钮 */
    .btn-icon {
      background: none; color: var(--text-muted); padding: 4px 8px;
      font-size: .82rem; border-radius: 4px;
    }
    .btn-icon:hover { background: var(--border); color: var(--text); }
    .btn-danger { background: rgba(248,113,113,.1); color: var(--error); }
    .btn-danger:hover { background: rgba(248,113,113,.2); }
  </style>
</head>
<body>

<!-- 登录页 -->
<div class="login-wrap" id="loginWrap">
  <div class="login-card">
    <h1>🔐 管理员登录</h1>
    <input type="password" id="pwdInput" placeholder="输入管理员密码">
    <button class="btn-primary" id="loginBtn" style="width:100%">登录</button>
    <p class="login-error" id="loginError"></p>
  </div>
</div>

<!-- 主界面 -->
<div class="app" id="app" style="display:none;flex-direction:row">
  <div class="sidebar">
    <h2>管理面板</h2>
    <button class="nav-item active" data-page="keys">🔑 密钥管理</button>
    <button class="nav-item" data-page="stats">📊 使用统计</button>
    <button class="nav-item" data-page="settings">⚙️ 设置</button>
  </div>
  <div class="main">

    <!-- 密钥管理页 -->
    <div class="page active" id="page-keys">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px">
        <span class="page-title" style="margin:0">密钥管理</span>
        <button class="btn-primary" id="addConfigBtn">+ 新增配置</button>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <table id="configTable">
          <thead>
            <tr>
              <th>名称</th><th>提供商</th><th>密钥</th>
              <th>状态</th><th>创建时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody id="configTbody"></tbody>
        </table>
      </div>
    </div>

    <!-- 统计页 -->
    <div class="page" id="page-stats">
      <p class="page-title">使用统计</p>
      <div class="stats-grid" id="statsGrid"></div>
    </div>

    <!-- 设置页 -->
    <div class="page" id="page-settings">
      <p class="page-title">设置</p>
      <div class="card">
        <p style="font-size:.9rem;color:var(--text-muted);line-height:1.8">
          管理员密码通过环境变量 <code style="background:var(--bg);padding:2px 6px;border-radius:4px">ADMIN_PASSWORD</code> 配置。<br>
          修改密码请在项目根目录的 <code style="background:var(--bg);padding:2px 6px;border-radius:4px">.env</code> 文件中更新该变量，然后重启 server.py 生效。
        </p>
      </div>
    </div>

  </div>
</div>

<!-- 新增/编辑弹窗 -->
<div class="modal-bg" id="modalBg">
  <div class="modal">
    <h3 id="modalTitle">新增配置</h3>
    <div class="form-group"><label>名称</label><input id="fName" placeholder="如 Claude Sonnet"></div>
    <div class="form-group"><label>提供商</label><input id="fProvider" placeholder="如 anthropic"></div>
    <div class="form-group"><label>Base URL</label><input id="fBaseUrl" placeholder="https://api.anthropic.com/v1"></div>
    <div class="form-group"><label>API Key</label><input id="fApiKey" type="password" placeholder="sk-ant-..."></div>
    <p style="color:var(--error);font-size:.83rem;min-height:18px" id="modalError"></p>
    <div class="modal-actions">
      <button class="btn-secondary" id="cancelBtn">取消</button>
      <button class="btn-primary" id="saveBtn">保存</button>
    </div>
  </div>
</div>

<script type="module">
import { login, getConfigs, createConfig, updateConfig, deleteConfig, getStats, getDailyStats } from '../assets/js/admin.js';

// ── 登录 ──────────────────────────────────────────────────
const loginWrap = document.getElementById('loginWrap');
const app       = document.getElementById('app');
const pwdInput  = document.getElementById('pwdInput');
const loginBtn  = document.getElementById('loginBtn');
const loginError = document.getElementById('loginError');

async function doLogin() {
  loginError.textContent = '';
  const ok = await login(pwdInput.value).catch(() => false);
  if (ok) {
    loginWrap.style.display = 'none';
    app.style.display = 'flex';
    loadKeys();
  } else {
    loginError.textContent = '密码错误，请重试';
  }
}
loginBtn.addEventListener('click', doLogin);
pwdInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

// ── 导航 ──────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    const page = document.getElementById('page-' + btn.dataset.page);
    page.classList.add('active');
    if (btn.dataset.page === 'stats') loadStats();
  });
});

// ── 密钥管理 ──────────────────────────────────────────────
const configTbody = document.getElementById('configTbody');
let editingId = null;

async function loadKeys() {
  const configs = await getConfigs().catch(() => []);
  configTbody.innerHTML = '';
  if (configs.length === 0) {
    configTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:32px">暂无配置，点击「新增配置」添加</td></tr>';
    return;
  }
  configs.forEach(c => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(c.name)}</td>
      <td>${esc(c.provider)}</td>
      <td><code>${esc(c.api_key)}</code></td>
      <td><span class="badge ${c.is_active ? 'badge-on' : 'badge-off'}">${c.is_active ? '启用' : '禁用'}</span></td>
      <td>${c.created_at.slice(0,10)}</td>
      <td>
        <button class="btn-icon" data-edit="${c.id}">编辑</button>
        <button class="btn-icon btn-danger" data-del="${c.id}">删除</button>
      </td>`;
    configTbody.appendChild(tr);
  });
  configTbody.querySelectorAll('[data-edit]').forEach(b =>
    b.addEventListener('click', () => openEdit(configs.find(c => c.id == b.dataset.edit))));
  configTbody.querySelectorAll('[data-del]').forEach(b =>
    b.addEventListener('click', () => confirmDelete(b.dataset.del)));
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── 弹窗 ──────────────────────────────────────────────────
const modalBg    = document.getElementById('modalBg');
const modalTitle = document.getElementById('modalTitle');
const modalError = document.getElementById('modalError');
const fName      = document.getElementById('fName');
const fProvider  = document.getElementById('fProvider');
const fBaseUrl   = document.getElementById('fBaseUrl');
const fApiKey    = document.getElementById('fApiKey');

document.getElementById('addConfigBtn').addEventListener('click', () => {
  editingId = null;
  modalTitle.textContent = '新增配置';
  fName.value = fProvider.value = fBaseUrl.value = fApiKey.value = '';
  modalError.textContent = '';
  modalBg.classList.add('open');
});

document.getElementById('cancelBtn').addEventListener('click', () => modalBg.classList.remove('open'));
modalBg.addEventListener('click', e => { if (e.target === modalBg) modalBg.classList.remove('open'); });

function openEdit(c) {
  editingId = c.id;
  modalTitle.textContent = '编辑配置';
  fName.value = c.name; fProvider.value = c.provider;
  fBaseUrl.value = c.base_url; fApiKey.value = '';
  modalError.textContent = '';
  modalBg.classList.add('open');
}

document.getElementById('saveBtn').addEventListener('click', async () => {
  modalError.textContent = '';
  if (!fName.value.trim() || !fProvider.value.trim() || !fBaseUrl.value.trim()) {
    modalError.textContent = '名称、提供商、Base URL 不能为空'; return;
  }
  if (!editingId && !fApiKey.value.trim()) {
    modalError.textContent = 'API Key 不能为空'; return;
  }
  const body = {
    name: fName.value.trim(), provider: fProvider.value.trim(),
    base_url: fBaseUrl.value.trim(), api_key: fApiKey.value.trim() || '(unchanged)',
    is_active: 1,
  };
  try {
    if (editingId) await updateConfig(editingId, body);
    else           await createConfig(body);
    modalBg.classList.remove('open');
    loadKeys();
  } catch (e) {
    modalError.textContent = '保存失败：' + e.message;
  }
});

async function confirmDelete(id) {
  if (!confirm('确定删除此配置及其所有统计记录？')) return;
  await deleteConfig(id).catch(() => {});
  loadKeys();
}

// ── 统计页 ────────────────────────────────────────────────
const statsGrid = document.getElementById('statsGrid');

async function loadStats() {
  const stats = await getStats().catch(() => []);
  statsGrid.innerHTML = '';
  if (stats.length === 0) {
    statsGrid.innerHTML = '<p style="color:var(--text-muted);font-size:.9rem">暂无统计数据，先在密钥管理页添加配置并发起聊天。</p>';
    return;
  }
  stats.forEach(s => {
    const card = document.createElement('div');
    card.className = 'stat-card';
    card.innerHTML = `
      <h3>${esc(s.name)} <span style="font-size:.75rem;color:var(--text-muted)">${esc(s.provider)}</span></h3>
      <div class="stat-row"><span>总调用次数</span><span>${s.total_calls}</span></div>
      <div class="stat-row"><span>成功率</span><span>${s.success_rate}%</span></div>
      <div class="stat-row"><span>累计 Token</span><span>${s.total_tokens.toLocaleString()}</span></div>
      <div class="stat-row"><span>平均响应时间</span><span>${s.avg_duration_ms} ms</span></div>
      <div class="stat-row"><span>最后使用</span><span>${s.last_used.slice(0,16).replace('T',' ')}</span></div>
      <div class="chart-wrap" id="chart-${s.config_id}">
        <div style="font-size:.75rem;color:var(--text-muted);margin-bottom:6px">最近 7 天调用趋势</div>
        <svg class="chart-svg" id="svg-${s.config_id}" viewBox="0 0 260 60" preserveAspectRatio="none"></svg>
        <div class="chart-label" id="label-${s.config_id}"></div>
      </div>`;
    card.addEventListener('click', () => toggleChart(s.config_id));
    statsGrid.appendChild(card);
  });
}

async function toggleChart(configId) {
  const wrap = document.getElementById('chart-' + configId);
  if (wrap.classList.contains('open')) { wrap.classList.remove('open'); return; }
  wrap.classList.add('open');
  const data = await getDailyStats(configId).catch(() => []);
  drawChart(configId, data);
}

function drawChart(configId, data) {
  const svg   = document.getElementById('svg-' + configId);
  const label = document.getElementById('label-' + configId);
  const days  = getLast7Days();
  const map   = Object.fromEntries(data.map(d => [d.day, d.count]));
  const vals  = days.map(d => map[d] || 0);
  const max   = Math.max(...vals, 1);
  const W = 260, H = 60, pad = 10;
  const xs = days.map((_, i) => pad + i * ((W - pad*2) / 6));
  const ys = vals.map(v => H - pad - (v / max) * (H - pad*2));
  const pts = xs.map((x, i) => `${x},${ys[i]}`).join(' ');
  svg.innerHTML = `
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    ${xs.map((x,i) => `<circle cx="${x}" cy="${ys[i]}" r="3" fill="var(--accent)"/>`).join('')}`;
  label.innerHTML = days.map(d => `<span>${d.slice(5)}</span>`).join('');
}

function getLast7Days() {
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    days.push(d.toISOString().slice(0,10));
  }
  return days;
}
</script>
</body>
</html>
```

- [ ] **Step 2: 验证文件大小**

```bash
ls -lh /Users/hw-edit/Desktop/h00484736/api-web-project/pages/admin.html
```

预期：> 8KB

- [ ] **Step 3: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add pages/admin.html
git commit -m "feat: add admin panel UI with login, key management, and stats"
```

---

### Task 6: 更新 index.html 注册管理员入口

**Files:**
- 修改: `index.html`（pages 数组）

- [ ] **Step 1: 在 pages 数组中追加管理员入口**

找到 `index.html` 中：
```js
const pages = [
  { icon: '🤖', title: 'Claude Chat', desc: '流式对话 · 多轮历史', href: 'pages/claude.html' },
];
```

替换为：
```js
const pages = [
  { icon: '🤖', title: 'Claude Chat', desc: '流式对话 · 多轮历史', href: 'pages/claude.html' },
  { icon: '⚙️', title: '管理员面板', desc: 'API 密钥 · 使用统计', href: 'pages/admin.html' },
];
```

- [ ] **Step 2: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add index.html
git commit -m "feat: register admin panel in homepage navigation"
```
