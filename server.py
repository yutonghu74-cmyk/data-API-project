import os
import sys
import json
import uuid
import anthropic
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal, Optional
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import Header
from fastapi.responses import JSONResponse

DB_PATH = os.path.join(os.path.dirname(__file__), "admin.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # 建议在 .env 中设置强密码

import hashlib
import secrets

def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL UNIQUE,
                password   TEXT NOT NULL,
                role       TEXT DEFAULT 'user',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_tokens (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER REFERENCES users(id),
                config_id    INTEGER REFERENCES api_configs(id),
                project_name TEXT NOT NULL,
                purpose      TEXT NOT NULL,
                lead         TEXT NOT NULL,
                budget       TEXT NOT NULL,
                sub_accounts TEXT DEFAULT '',
                status       TEXT DEFAULT 'pending',
                review_note  TEXT DEFAULT '',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_configs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                base_url   TEXT NOT NULL,
                api_key    TEXT NOT NULL,
                provider   TEXT NOT NULL,
                models     TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active  INTEGER DEFAULT 1
            )
        """)
        # 兼容旧库：若 models 列不存在则添加
        try:
            conn.execute("ALTER TABLE api_configs ADD COLUMN models TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                config_id  INTEGER REFERENCES api_configs(id),
                model      TEXT,
                title      TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER REFERENCES chat_sessions(id),
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        # 初始化默认管理员账号（仅首次，已存在则跳过）
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                "INSERT INTO users (username, password, role, created_at) VALUES (?,?,?,?)",
                ("admin", hash_password("admin123"), "admin", now)
            )
            conn.commit()
            print("INFO: 默认管理员账号已创建 (admin / admin123)")
        except Exception:
            pass  # 已存在则跳过

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
    import hmac
    if not hmac.compare_digest(x_admin_password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

_env_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not _env_api_key:
    print("WARNING: ANTHROPIC_API_KEY not set. Will use key from admin database.")

def get_active_anthropic_key() -> str | None:
    """从 api_configs 表读取 provider=anthropic 且 is_active=1 的最新密钥。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key FROM api_configs WHERE provider='anthropic' AND is_active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["api_key"] if row else None
    except Exception:
        return None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "null"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7"]  # fallback only

class TextBlock(BaseModel):
    type: Literal["text"]
    text: str

class ImageBlock(BaseModel):
    type: Literal["image"]
    source: dict

class DocumentBlock(BaseModel):
    type: Literal["document"]
    source: dict

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[TextBlock | ImageBlock | DocumentBlock]

class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "claude-sonnet-4-6"
    system: str = ""
    config_id: int | None = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/models")
def models():
    return {"models": MODELS}

@app.get("/configs/{config_id}/models")
def config_models(config_id: int):
    """返回指定配置的可用模型列表。"""
    with get_db() as conn:
        row = conn.execute("SELECT models FROM api_configs WHERE id=?", (config_id,)).fetchone()
    if not row or not row["models"]:
        return {"models": []}
    return {"models": [m.strip() for m in row["models"].split(",") if m.strip()]}

@app.get("/active-configs")
def active_configs():
    """返回所有激活的 anthropic 配置列表（仅 id 和 name，不暴露密钥）。"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name FROM api_configs WHERE provider='anthropic' AND is_active=1 ORDER BY id DESC"
            ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]
    except Exception:
        return []

@app.get("/active-config")
def active_config():
    """返回当前激活的 anthropic 配置 ID，供前端带入 /chat 请求以记录统计。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM api_configs WHERE provider='anthropic' AND is_active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {"config_id": row["id"] if row else None}
    except Exception:
        return {"config_id": None}

@app.post("/chat")
async def chat(req: ChatRequest):
    if req.model not in MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {req.model}")

    start_time = time.time()
    called_at  = datetime.now(timezone.utc).isoformat()

    def generate():
        _key = get_active_anthropic_key() or _env_api_key
        if not _key:
            yield f"data: {json.dumps({'error': '未配置 API Key，请在管理员面板添加 Anthropic 配置'})}\n\n"
            return
        _client = anthropic.Anthropic(api_key=_key)
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

            with _client.messages.stream(**kwargs) as stream:
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

# ── Admin: configs ────────────────────────────────────────

class ConfigIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    provider: str
    models: str = ""
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
            "INSERT INTO api_configs (name,base_url,api_key,provider,models,created_at,updated_at,is_active) VALUES (?,?,?,?,?,?,?,?)",
            (body.name, body.base_url, body.api_key, body.provider, body.models, now, now, body.is_active)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM api_configs WHERE id=?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["api_key"] = "****" + d["api_key"][-4:] if len(d["api_key"]) >= 4 else "****"
    return d

@app.put("/admin/configs/{config_id}")
def update_config(config_id: int, body: ConfigIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        if body.api_key and body.api_key != "(unchanged)":
            conn.execute(
                "UPDATE api_configs SET name=?,base_url=?,api_key=?,provider=?,models=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.api_key, body.provider, body.models, now, body.is_active, config_id)
            )
        else:
            conn.execute(
                "UPDATE api_configs SET name=?,base_url=?,provider=?,models=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.provider, body.models, now, body.is_active, config_id)
            )
        conn.commit()
        row = conn.execute("SELECT * FROM api_configs WHERE id=?", (config_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    d = dict(row)
    d["api_key"] = "****" + d["api_key"][-4:] if len(d["api_key"]) >= 4 else "****"
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

# ── Auth ──────────────────────────────────────────────────

class RegisterIn(BaseModel):
    username: str
    password: str

class LoginIn(BaseModel):
    username: str
    password: str

def get_current_user(x_token: str = Header(default="")):
    if not x_token:
        raise HTTPException(status_code=401, detail="未登录")
    with get_db() as conn:
        row = conn.execute(
            "SELECT u.* FROM users u JOIN user_tokens t ON t.user_id=u.id WHERE t.token=?", (x_token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="登录已过期")
    return dict(row)

@app.post("/auth/register")
def register(body: RegisterIn):
    if not body.username.strip() or not body.password.strip():
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password, created_at) VALUES (?,?,?)",
                (body.username.strip(), hash_password(body.password), now)
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="用户名已存在")
    return {"ok": True}

@app.post("/auth/login")
def user_login(body: LoginIn):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (body.username.strip(), hash_password(body.password))
        ).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute("INSERT INTO user_tokens (token, user_id, created_at) VALUES (?,?,?)",
                     (token, user["id"], now))
        conn.commit()
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}

@app.post("/auth/logout")
def user_logout(x_token: str = Header(default="")):
    with get_db() as conn:
        conn.execute("DELETE FROM user_tokens WHERE token=?", (x_token,))
        conn.commit()
    return {"ok": True}

@app.get("/auth/me")
def me(x_token: str = Header(default="")):
    return get_current_user(x_token)

# ── API 申请 ───────────────────────────────────────────────

class RequestIn(BaseModel):
    config_id: int
    project_name: str
    purpose: str
    lead: str
    budget: str
    sub_accounts: str = ""

@app.post("/api-requests")
def create_request(body: RequestIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO api_requests (user_id,config_id,project_name,purpose,lead,budget,sub_accounts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (user["id"], body.config_id, body.project_name, body.purpose, body.lead, body.budget, body.sub_accounts, now, now)
        )
        conn.commit()
    return {"id": cur.lastrowid}

@app.get("/api-requests/my")
def my_requests(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.*, c.name as config_name, c.provider, c.base_url
            FROM api_requests r
            JOIN api_configs c ON c.id = r.config_id
            WHERE r.user_id=?
            ORDER BY r.created_at DESC
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api-requests/approved")
def approved_configs(x_token: str = Header(default="")):
    """返回当前用户已审核通过的 API 配置列表（用于聊天页下拉）。"""
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.provider, c.base_url, c.created_at, c.updated_at,
                   r.id as request_id
            FROM api_requests r
            JOIN api_configs c ON c.id = r.config_id
            WHERE r.user_id=? AND r.status='approved' AND c.is_active=1
            ORDER BY c.name
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]

# ── Admin: 申请审核 ────────────────────────────────────────

class ReviewIn(BaseModel):
    status: Literal["approved", "rejected"]
    review_note: str = ""

@app.get("/admin/api-requests")
def admin_list_requests(x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.*, u.username, c.name as config_name, c.provider
            FROM api_requests r
            JOIN users u ON u.id = r.user_id
            JOIN api_configs c ON c.id = r.config_id
            ORDER BY r.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

@app.put("/admin/api-requests/{req_id}")
def admin_review_request(req_id: int, body: ReviewIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE api_requests SET status=?, review_note=?, updated_at=? WHERE id=?",
            (body.status, body.review_note, now, req_id)
        )
        conn.commit()
    return {"ok": True}

# ── Admin: 用户管理 ───────────────────────────────────────

@app.get("/admin/users")
def list_users(x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]

class UserRoleIn(BaseModel):
    role: Literal["user", "admin"]

@app.put("/admin/users/{user_id}/role")
def update_user_role(user_id: int, body: UserRoleIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
        conn.commit()
    return {"ok": True}

class ResetPwdIn(BaseModel):
    password: str

@app.put("/admin/users/{user_id}/password")
def reset_user_password(user_id: int, body: ResetPwdIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6位")
    with get_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        conn.execute("UPDATE users SET password=? WHERE id=?", (hash_password(body.password), user_id))
        conn.commit()
    return {"ok": True}

@app.delete("/admin/users/{user_id}")
def delete_user(user_id: int, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        # 不允许删除最后一个管理员
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        user = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if user and user["role"] == "admin" and admin_count <= 1:
            raise HTTPException(status_code=400, detail="不能删除唯一的管理员账号")
        conn.execute("DELETE FROM user_tokens WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    return {"ok": True}

# ── 对话历史 ──────────────────────────────────────────────

class SessionIn(BaseModel):
    config_id: int | None = None
    model: str = ""
    title: str = ""

class MessageIn(BaseModel):
    session_id: int
    role: Literal["user", "assistant"]
    content: str

@app.post("/sessions")
def create_session(body: SessionIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (user_id, config_id, model, title, created_at) VALUES (?,?,?,?,?)",
            (user["id"], body.config_id, body.model, body.title, now)
        )
        conn.commit()
    return {"session_id": cur.lastrowid}

@app.post("/sessions/messages")
def save_message(body: MessageIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    # content 若为列表（多模态）则只保存文本部分
    content = body.content if isinstance(body.content, str) else str(body.content)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (body.session_id, body.role, content, now)
        )
        conn.commit()
    return {"ok": True}

@app.get("/history/my")
def my_history(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, c.name as config_name,
                   (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as msg_count
            FROM chat_sessions s
            LEFT JOIN api_configs c ON c.id = s.config_id
            WHERE s.user_id = ?
            ORDER BY s.created_at DESC LIMIT 100
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.get("/history/{session_id}")
def get_session_messages(session_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        # 仅本人或管理员可查看
        if session["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="无权限")
        msgs = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at",
            (session_id,)
        ).fetchall()
    return {"session": dict(session), "messages": [dict(m) for m in msgs]}

@app.get("/admin/history")
def admin_history(user_id: int | None = None, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        if user_id:
            rows = conn.execute("""
                SELECT s.*, u.username, c.name as config_name,
                       (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as msg_count
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN api_configs c ON c.id = s.config_id
                WHERE s.user_id = ?
                ORDER BY s.created_at DESC LIMIT 200
            """, (user_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT s.*, u.username, c.name as config_name,
                       (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as msg_count
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN api_configs c ON c.id = s.config_id
                ORDER BY s.created_at DESC LIMIT 200
            """).fetchall()
    return [dict(r) for r in rows]

# ── ClickHouse ────────────────────────────────────────────

def get_ch_client():
    try:
        import clickhouse_connect
        return clickhouse_connect.get_client(
            host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
            port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            database=os.environ.get("CLICKHOUSE_DB", "default"),
            username=os.environ.get("CLICKHOUSE_USER", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ClickHouse 连接失败：{e}")

def ensure_ch_tables():
    try:
        ch = get_ch_client()
        ch.command("""
            CREATE TABLE IF NOT EXISTS batch_tasks (
                task_id     String,
                task_name   String,
                label       String,
                row_count   UInt32,
                status      String,
                config_name String,
                created_at  DateTime DEFAULT now()
            ) ENGINE = MergeTree() ORDER BY created_at
        """)
        ch.command("""
            CREATE TABLE IF NOT EXISTS batch_inputs (
                task_id     String,
                row_index   UInt32,
                input_json  String,
                created_at  DateTime DEFAULT now()
            ) ENGINE = MergeTree() ORDER BY (task_id, row_index)
        """)
        ch.command("""
            CREATE TABLE IF NOT EXISTS batch_results (
                task_id     String,
                row_index   UInt32,
                input_json  String,
                output_text String,
                output_type String,
                output_path String,
                label       String,
                model       String,
                success     UInt8,
                error_msg   String,
                created_at  DateTime DEFAULT now()
            ) ENGINE = MergeTree() ORDER BY (task_id, row_index)
        """)
    except Exception:
        pass  # ClickHouse 未配置时静默忽略

@app.get("/clickhouse/status")
def ch_status():
    """检查 ClickHouse 连接状态。"""
    try:
        ch = get_ch_client()
        ch.command("SELECT 1")
        return {"connected": True}
    except Exception as e:
        return {"connected": False, "error": str(e)}

# ── 批量处理 ───────────────────────────────────────────────

class BatchStartIn(BaseModel):
    task_name: str
    label: str = ""
    row_count: int
    config_id: int | None = None
    config_name: str = ""
    model: str = ""

class BatchRowIn(BaseModel):
    task_id: str
    row_index: int
    input_json: str
    output_text: str = ""
    output_type: str = "text"
    output_path: str = ""
    label: str = ""
    model: str = ""
    success: int = 1
    error_msg: str = ""

@app.post("/batch/start")
def batch_start(body: BatchStartIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    task_id = str(uuid.uuid4())
    try:
        ensure_ch_tables()
        ch = get_ch_client()
        ch.insert("batch_tasks", [[
            task_id, body.task_name, body.label,
            body.row_count, "running", body.config_name
        ]], column_names=["task_id","task_name","label","row_count","status","config_name"])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ClickHouse 写入失败：{e}")
    return {"task_id": task_id}

class BatchInputsIn(BaseModel):
    task_id: str
    rows: list[dict]  # [{row_index: int, input_json: str}, ...]

@app.post("/batch/inputs")
def batch_inputs(body: BatchInputsIn, x_token: str = Header(default="")):
    """批量写入输入数据到 ClickHouse。"""
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        data = [[body.task_id, r["row_index"], r["input_json"]] for r in body.rows]
        ch.insert("batch_inputs", data, column_names=["task_id","row_index","input_json"])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ClickHouse 写入失败：{e}")
    return {"ok": True, "count": len(body.rows)}

@app.get("/batch/inputs/{task_id}")
def get_batch_inputs(task_id: str, x_token: str = Header(default="")):
    """从 ClickHouse 读取某任务的输入数据。"""
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        rows = ch.query(
            "SELECT row_index, input_json FROM batch_inputs WHERE task_id=%(tid)s ORDER BY row_index",
            parameters={"tid": task_id}
        )
        return [{"row_index": r[0], "input_json": r[1]} for r in rows.result_rows]
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.post("/batch/row")
def batch_row(body: BatchRowIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        ch.insert("batch_results", [[
            body.task_id, body.row_index, body.input_json,
            body.output_text, body.output_type, body.output_path,
            body.label, body.model, body.success, body.error_msg
        ]], column_names=[
            "task_id","row_index","input_json","output_text",
            "output_type","output_path","label","model","success","error_msg"
        ])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ClickHouse 写入失败：{e}")
    return {"ok": True}

@app.put("/batch/finish/{task_id}")
def batch_finish(task_id: str, x_token: str = Header(default="")):
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        ch.command(f"ALTER TABLE batch_tasks UPDATE status='completed' WHERE task_id='{task_id}'")
    except Exception:
        pass
    return {"ok": True}

@app.get("/batch/tasks")
def batch_tasks(x_token: str = Header(default="")):
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        rows = ch.query("SELECT * FROM batch_tasks ORDER BY created_at DESC LIMIT 100")
        return [dict(zip(rows.column_names, r)) for r in rows.result_rows]
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/batch/results/{task_id}")
def batch_results(task_id: str, x_token: str = Header(default="")):
    get_current_user(x_token)
    try:
        ch = get_ch_client()
        rows = ch.query(
            "SELECT * FROM batch_results WHERE task_id=%(tid)s ORDER BY row_index",
            parameters={"tid": task_id}
        )
        return [dict(zip(rows.column_names, r)) for r in rows.result_rows]
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

# ── 保存结果到本地文件 ─────────────────────────────────────

class SaveResultIn(BaseModel):
    path: str
    content: str

@app.post("/save-result")
def save_result(body: SaveResultIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    try:
        with open(body.path, 'w', encoding='utf-8') as f:
            f.write(body.content)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
