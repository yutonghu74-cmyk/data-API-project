import os
import sys
import json
import uuid
import anthropic
from dotenv import load_dotenv

load_dotenv()

import asyncio
from fastapi import FastAPI, HTTPException, Header, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager

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
        # 兼容旧库：新增字段
        for col, definition in [
            ("models",          "TEXT DEFAULT ''"),
            ("price_input",     "REAL DEFAULT 0"),   # 每千 input token 价格（元）
            ("price_output",    "REAL DEFAULT 0"),   # 每千 output token 价格（元）
            ("manager",         "TEXT DEFAULT ''"),  # 该 API 的负责管理员
        ]:
            try:
                conn.execute(f"ALTER TABLE api_configs ADD COLUMN {col} {definition}")
                conn.commit()
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id     INTEGER REFERENCES api_configs(id),
                user_id       INTEGER REFERENCES users(id),
                called_at     TEXT NOT NULL,
                model         TEXT,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost          REAL DEFAULT 0,
                success       INTEGER DEFAULT 1,
                duration_ms   INTEGER DEFAULT 0,
                error_msg     TEXT
            )
        """)
        # 兼容旧 usage_stats
        for col, definition in [
            ("user_id", "INTEGER DEFAULT NULL"),
            ("cost",    "REAL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE usage_stats ADD COLUMN {col} {definition}")
                conn.commit()
            except Exception:
                pass
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
        # Spec 1 三层结构(accounts → sub_accounts → api_keys)
        # 生产中由 migrations/v1_account_schema.py 创建;tests 用 init_db 兜底
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
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
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sub_accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id              INTEGER PRIMARY KEY,
                sub_account_id  INTEGER NOT NULL REFERENCES sub_accounts(id) ON DELETE RESTRICT,
                name            TEXT NOT NULL,
                api_key         TEXT NOT NULL,
                is_active       INTEGER DEFAULT 1,
                exhausted       INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL
            )
        """)
        # chat_sessions 兼容添加 pinned 字段
        try:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN pinned INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        # batch_jobs / batch_job_rows（SQLite 批量历史，不依赖 ClickHouse）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                task_name  TEXT NOT NULL,
                model      TEXT DEFAULT '',
                config_id  INTEGER,
                label      TEXT DEFAULT '',
                row_count  INTEGER DEFAULT 0,
                done_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                status     TEXT DEFAULT 'running',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_job_rows (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER REFERENCES batch_jobs(id),
                row_index   INTEGER NOT NULL,
                input_json  TEXT NOT NULL,
                output_text TEXT DEFAULT '',
                success     INTEGER DEFAULT 1,
                error_msg   TEXT DEFAULT ''
            )
        """)
        # api_requests 兼容添加字段
        for col, definition in [
            ("sub_account_id", "INTEGER DEFAULT NULL"),
            ("cc_person",      "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE api_requests ADD COLUMN {col} {definition}")
            except Exception:
                pass
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
        raise HTTPException(status_code=403, detail="Forbidden")

_env_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not _env_api_key:
    print("WARNING: ANTHROPIC_API_KEY not set. Will use key from admin database.")

def get_active_anthropic_key() -> str | None:
    """从 api_configs 表读取 provider=anthropic 且 is_active=1 的最新密钥（兜底用）。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key FROM api_configs WHERE provider='anthropic' AND is_active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["api_key"] if row else None
    except Exception:
        return None

def get_config_credentials(config_id: int | None) -> tuple[str | None, str | None, str | None]:
    """返回 (api_key, base_url, provider)。优先用指定 config_id，否则兜底找 anthropic provider。"""
    if config_id:
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT api_key, base_url, provider FROM api_configs WHERE id=? AND is_active=1",
                    (config_id,)
                ).fetchone()
            if row:
                return row["api_key"], row["base_url"], row["provider"]
        except Exception:
            pass
    return get_active_anthropic_key() or _env_api_key, None, "anthropic"

def _friendly_error(e: Exception) -> str:
    """把 API 原始异常转成简洁的中文提示。"""
    raw = str(e)
    import re as _re
    # 提取 message 字段
    m = _re.search(r"'message':\s*'([^']+)'", raw)
    if not m:
        m = _re.search(r'"message":\s*"([^"]+)"', raw)
    if m:
        msg = m.group(1)
        # 常见错误的友好提示
        if 'model_not_found' in raw or 'No available channel' in raw:
            return f"模型不可用：{msg}\n请检查密钥管理中的模型名称是否正确。"
        if 'invalid api key' in raw.lower() or 'authentication' in raw.lower():
            return f"API Key 无效或已过期，请在密钥管理中重新配置。"
        if 'rate limit' in raw.lower():
            return f"请求频率超限，请稍后再试。"
        if 'context_length' in raw.lower() or 'too long' in raw.lower():
            return f"消息过长，请清空对话后重试。"
        return msg  # 返回提取到的 message，已比原始报错简洁
    # 提取状态码
    m2 = _re.search(r'Error code: (\d+)', raw)
    if m2:
        code = m2.group(1)
        return f"API 返回错误（HTTP {code}），请检查配置是否正确。"
    return "请求失败，请检查 API 配置后重试。"

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
    user_token: str | None = None  # 用于记录调用用户

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/models")
def models():
    return {"models": MODELS}

@app.get("/admin/configs/{config_id}/fetch-models")
def fetch_models_from_provider(config_id: int, x_admin_password: str = Header(default="")):
    """从供应商 /v1/models 接口拉取真实可用的模型列表。"""
    require_admin(x_admin_password)
    with get_db() as conn:
        row = conn.execute(
            "SELECT api_key, base_url, provider FROM api_configs WHERE id=?", (config_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="配置不存在")

    base_url = (row["base_url"] or "").rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"

    try:
        import httpx
        resp = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {row['api_key']}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = []
        # OpenAI 格式: {"data": [{"id": "..."}]}
        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
        # {"models": [...]}
        elif isinstance(data, dict) and "models" in data:
            raw = data["models"]
            models = [m["id"] if isinstance(m, dict) else str(m) for m in raw]
        # 纯列表
        elif isinstance(data, list):
            models = [m["id"] if isinstance(m, dict) else str(m) for m in data]
        return {"models": sorted(models), "empty": not models}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"无法连接供应商：{e}")

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
    """返回所有激活的配置列表（仅 id、name、provider，不暴露密钥）。"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, provider, manager FROM api_configs WHERE is_active=1 ORDER BY id DESC"
            ).fetchall()
        return [{"id": r["id"], "name": r["name"], "provider": r["provider"], "manager": r["manager"] or ""} for r in rows]
    except Exception:
        return []

@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.model:
        raise HTTPException(status_code=400, detail="model 不能为空")

    start_time = time.time()
    called_at  = datetime.now(timezone.utc).isoformat()

    def generate():
        _key, _base_url, _provider = get_config_credentials(req.config_id)
        if not _key:
            yield f"data: {json.dumps({'error': '未配置 API Key，请在管理员面板添加配置'})}\n\n"
            return

        # 规范化 base_url
        _normalized_url = (_base_url or '').rstrip('/')

        # 判断是否使用 Anthropic 原生格式
        _use_anthropic = (_provider or '').lower() == 'anthropic' or (
            'anthropic' in _normalized_url and 'api.anthropic.com' in _normalized_url
        )

        input_tokens  = 0
        output_tokens = 0
        success       = 1
        error_msg     = None
        try:
            if _use_anthropic:
                # Anthropic 原生 SDK
                _ak = {"api_key": _key}
                if _normalized_url:
                    _base = _normalized_url[:-3].rstrip('/') if _normalized_url.endswith('/v1') else _normalized_url
                    _ak["base_url"] = _base
                _client = anthropic.Anthropic(**_ak)
                kwargs = dict(model=req.model, max_tokens=4096,
                              messages=[m.model_dump() for m in req.messages])
                if req.system:
                    kwargs["system"] = req.system
                with _client.messages.stream(**kwargs) as stream:
                    for text in stream.text_stream:
                        yield f"data: {json.dumps({'text': text})}\n\n"
                    usage = stream.get_final_message().usage
                    input_tokens  = usage.input_tokens
                    output_tokens = usage.output_tokens
            else:
                # OpenAI 兼容格式（一步API、New API 等代理）
                from openai import OpenAI as _OpenAI
                _base = _normalized_url if _normalized_url else "https://api.openai.com/v1"
                if not _normalized_url.endswith('/v1'):
                    _base = _normalized_url + '/v1' if _normalized_url else "https://api.openai.com/v1"
                _oc = _OpenAI(api_key=_key, base_url=_base)
                # 拼装 messages：Anthropic 格式 → OpenAI 格式
                oai_msgs = []
                if req.system:
                    oai_msgs.append({"role": "system", "content": req.system})
                for m in req.messages:
                    raw = m.model_dump()
                    # content 可能是 JSON 字符串（从 DB 加载的历史消息）
                    if isinstance(raw.get("content"), str):
                        try:
                            import json as _json
                            parsed = _json.loads(raw["content"])
                            if isinstance(parsed, list):
                                raw["content"] = parsed
                        except Exception:
                            pass
                    if isinstance(raw.get("content"), list):
                        oai_content = []
                        for block in raw["content"]:
                            btype = block.get("type", "")
                            if btype == "text":
                                oai_content.append({"type": "text", "text": block.get("text", "")})
                            elif btype == "image":
                                src = block.get("source", {})
                                if src.get("type") == "base64":
                                    url = f"data:{src['media_type']};base64,{src['data']}"
                                    oai_content.append({"type": "image_url", "image_url": {"url": url}})
                        oai_msgs.append({"role": raw["role"], "content": oai_content})
                    else:
                        oai_msgs.append({"role": raw["role"], "content": raw["content"]})
                stream = _oc.chat.completions.create(
                    model=req.model, messages=oai_msgs,
                    max_tokens=4096, stream=True
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield f"data: {json.dumps({'text': delta.content})}\n\n"
                    # 从最后一个 chunk 获取用量（部分代理支持）
                    if hasattr(chunk, 'usage') and chunk.usage:
                        input_tokens  = chunk.usage.prompt_tokens or 0
                        output_tokens = chunk.usage.completion_tokens or 0
            yield "data: [DONE]\n\n"
        except Exception as e:
            success   = 0
            error_msg = str(e)
            yield f"data: {json.dumps({'error': _friendly_error(e)})}\n\n"
        finally:
            if req.config_id is not None:
                duration_ms = int((time.time() - start_time) * 1000)
                try:
                    with get_db() as conn:
                        # 获取该配置的定价
                        cfg = conn.execute(
                            "SELECT price_input, price_output FROM api_configs WHERE id=?", (req.config_id,)
                        ).fetchone()
                        pi = cfg["price_input"]  if cfg else 0
                        po = cfg["price_output"] if cfg else 0
                        cost = (input_tokens / 1000.0 * pi) + (output_tokens / 1000.0 * po)

                        # 获取调用用户 ID
                        uid = None
                        if req.user_token:
                            row = conn.execute(
                                "SELECT user_id FROM user_tokens WHERE token=?", (req.user_token,)
                            ).fetchone()
                            if row: uid = row["user_id"]

                        conn.execute(
                            "INSERT INTO usage_stats (config_id,user_id,called_at,model,input_tokens,output_tokens,cost,success,duration_ms,error_msg) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (req.config_id, uid, called_at, req.model, input_tokens, output_tokens, cost, success, duration_ms, error_msg)
                        )
                        conn.commit()
                except Exception:
                    pass

    return StreamingResponse(generate(), media_type="text/event-stream")

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
        conn.execute("PRAGMA foreign_keys = ON")
        acc = conn.execute(
            "SELECT * FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not acc:
            raise HTTPException(status_code=404, detail="不存在")
        require_owner_or_admin(user, acc)
        used = conn.execute("""
            SELECT 1 FROM usage_stats us
            JOIN api_keys k     ON k.id = us.config_id
            JOIN sub_accounts s ON s.id = k.sub_account_id
            WHERE s.account_id = ?
            LIMIT 1
        """, (account_id,)).fetchone()
        if used:
            raise HTTPException(status_code=400, detail="该帐号下有 API 已被调用,不能删除")
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit()
    return


# ── Admin: sub-accounts ───────────────────────────────────

@app.get("/admin/accounts/{account_id}/sub-accounts")
def list_sub_accounts_new(account_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not acc:
            raise HTTPException(404, "不存在")
        if user["role"] != "admin":
            if acc["created_by"] != user["id"] and acc["manager_user_id"] != user["id"]:
                raise HTTPException(403, "无权访问")
        rows = conn.execute(
            "SELECT * FROM sub_accounts WHERE account_id=? ORDER BY id DESC",
            (account_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/accounts/{account_id}/sub-accounts")
def create_sub_account_new(account_id: int, body: SubAccountIn,
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
               a.manager_user_id AS account_manager,
               a.provider AS account_provider
        FROM sub_accounts s JOIN accounts a ON a.id = s.account_id
        WHERE s.id = ?
    """, (sub_id,)).fetchone()
    if not row:
        raise HTTPException(404, "子帐号不存在")
    return row


@app.put("/admin/sub-accounts/{sub_id}")
def update_sub_account_new(sub_id: int, body: SubAccountIn,
                            x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
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
def delete_sub_account_new(sub_id: int, x_token: str = Header(default="")):
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


# ── Admin: configs ────────────────────────────────────────

class ConfigIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    provider: str
    models: str = ""
    price_input: float = 0   # 元/千 input token
    price_output: float = 0  # 元/千 output token
    is_active: int = 1
    manager: str = ""

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
            "INSERT INTO api_configs (name,base_url,api_key,provider,models,price_input,price_output,manager,created_at,updated_at,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (body.name, body.base_url, body.api_key, body.provider, body.models, body.price_input, body.price_output, body.manager, now, now, body.is_active)
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
                "UPDATE api_configs SET name=?,base_url=?,api_key=?,provider=?,models=?,price_input=?,price_output=?,manager=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.api_key, body.provider, body.models, body.price_input, body.price_output, body.manager, now, body.is_active, config_id)
            )
        else:
            conn.execute(
                "UPDATE api_configs SET name=?,base_url=?,provider=?,models=?,price_input=?,price_output=?,manager=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.provider, body.models, body.price_input, body.price_output, body.manager, now, body.is_active, config_id)
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
def list_stats(days: int | None = None, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    date_clause = f"AND called_at >= date('now','-{int(days)} days')" if days else ""
    with get_db() as conn:
        configs = conn.execute("SELECT id, name, provider, created_at FROM api_configs").fetchall()
        result = []
        for cfg in configs:
            cid = cfg["id"]
            row = conn.execute(f"""
                SELECT COUNT(*) as total,
                       SUM(success) as ok,
                       SUM(input_tokens+output_tokens) as tokens,
                       SUM(input_tokens) as in_tokens,
                       SUM(output_tokens) as out_tokens,
                       ROUND(SUM(cost),4) as total_cost,
                       AVG(duration_ms) as avg_ms,
                       MAX(called_at) as last_used
                FROM usage_stats WHERE config_id=? {date_clause}
            """, (cid,)).fetchone()
            total = row["total"] or 0
            ok    = row["ok"] or 0
            result.append({
                "config_id":       cid,
                "name":            cfg["name"],
                "provider":        cfg["provider"],
                "created_at":      cfg["created_at"],
                "total_calls":     total,
                "success_rate":    round(ok / total * 100, 1) if total else 0,
                "input_tokens":    row["in_tokens"] or 0,
                "output_tokens":   row["out_tokens"] or 0,
                "total_tokens":    row["tokens"] or 0,
                "total_cost":      row["total_cost"] or 0,
                "avg_duration_ms": round(row["avg_ms"] or 0, 1),
                "last_used":       row["last_used"] or "—",
            })
    return result

@app.get("/admin/stats/by-user")
def stats_by_user(x_admin_password: str = Header(default="")):
    """按用户分组的调用统计（token + 费用）。"""
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(u.username, '匿名') as username,
                COUNT(*) as total_calls,
                SUM(s.input_tokens)  as input_tokens,
                SUM(s.output_tokens) as output_tokens,
                SUM(s.input_tokens + s.output_tokens) as total_tokens,
                ROUND(SUM(s.cost), 4) as total_cost,
                SUM(s.success) as success_count,
                MAX(s.called_at) as last_used
            FROM usage_stats s
            LEFT JOIN users u ON u.id = s.user_id
            GROUP BY s.user_id
            ORDER BY total_cost DESC
        """).fetchall()
    return [dict(r) for r in rows]

@app.get("/admin/stats/{config_id}/daily")
def daily_stats(config_id: int, days: int = 7, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    n = max(1, int(days))
    with get_db() as conn:
        rows = conn.execute("""
            SELECT substr(called_at,1,10) as day, COUNT(*) as cnt
            FROM usage_stats
            WHERE config_id=?
              AND called_at >= date('now',? || ' days')
            GROUP BY day ORDER BY day
        """, (config_id, f"-{n}")).fetchall()
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
    cc_person: str = ""

@app.post("/api-requests")
def create_request(body: RequestIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO api_requests (user_id,config_id,project_name,purpose,lead,budget,sub_accounts,cc_person,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user["id"], body.config_id, body.project_name, body.purpose, body.lead, body.budget, body.sub_accounts, body.cc_person, now, now)
        )
        conn.commit()
    return {"id": cur.lastrowid}

@app.get("/api-requests/my")
def my_requests(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.*, c.name as config_name, c.provider, c.base_url, c.manager
            FROM api_requests r
            JOIN api_configs c ON c.id = r.config_id
            WHERE r.user_id=?
            ORDER BY r.created_at DESC
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
            SELECT r.*, u.username, c.name as config_name, c.provider, c.manager
            FROM api_requests r
            JOIN users u ON u.id = r.user_id
            JOIN api_configs c ON c.id = r.config_id
            ORDER BY r.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


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

class SessionRenameIn(BaseModel):
    title: str

@app.put("/sessions/{session_id}/title")
def rename_session(session_id: int, body: SessionRenameIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        row = conn.execute("SELECT user_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")
        if row["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="无权限")
        conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (body.title.strip(), session_id))
        conn.commit()
    return {"ok": True}

@app.put("/sessions/{session_id}/pin")
def pin_session(session_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        row = conn.execute("SELECT user_id, pinned FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")
        if row["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="无权限")
        new_pinned = 0 if row["pinned"] else 1
        conn.execute("UPDATE chat_sessions SET pinned=? WHERE id=?", (new_pinned, session_id))
        conn.commit()
    return {"pinned": bool(new_pinned)}

@app.delete("/sessions/{session_id}")
def delete_session(session_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        row = conn.execute("SELECT user_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")
        if row["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="无权限")
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        conn.commit()
    return {"ok": True}

@app.post("/sessions/messages")
def save_message(body: MessageIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    # content 若为列表，序列化为 JSON 字符串完整保存（含图片 base64）
    import json as _json
    content = body.content if isinstance(body.content, str) else _json.dumps(body.content, ensure_ascii=False)
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
            ORDER BY s.pinned DESC, s.created_at DESC LIMIT 100
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

# ── 子账号管理 ─────────────────────────────────────────────

class SubAccountIn(BaseModel):
    name: str
    description: str = ""
    available_models: str = ""
    quota_type: str = "unlimited"
    quota_amount: float = 0
    ip_restriction: str = ""
    is_active: int = 1

@app.get("/admin/configs/{config_id}/sub-accounts")
def list_sub_accounts(config_id: int, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sub_accounts WHERE config_id=? ORDER BY id DESC", (config_id,)
        ).fetchall()
    return [dict(r) for r in rows]

@app.post("/admin/configs/{config_id}/sub-accounts")
def create_sub_account(config_id: int, body: SubAccountIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO sub_accounts (config_id,name,description,available_models,quota_type,quota_amount,ip_restriction,is_active,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (config_id, body.name, body.description, body.available_models, body.quota_type, body.quota_amount, body.ip_restriction, body.is_active, now)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sub_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)

@app.put("/admin/sub-accounts/{sub_id}")
def update_sub_account(sub_id: int, body: SubAccountIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        conn.execute(
            "UPDATE sub_accounts SET name=?,description=?,available_models=?,quota_type=?,quota_amount=?,ip_restriction=?,is_active=? WHERE id=?",
            (body.name, body.description, body.available_models, body.quota_type, body.quota_amount, body.ip_restriction, body.is_active, sub_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sub_accounts WHERE id=?", (sub_id,)).fetchone()
    return dict(row)

@app.delete("/admin/sub-accounts/{sub_id}")
def delete_sub_account(sub_id: int, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    with get_db() as conn:
        conn.execute("DELETE FROM sub_accounts WHERE id=?", (sub_id,))
        conn.commit()
    return {"ok": True}

@app.put("/admin/api-requests/{req_id}")
def admin_review_request(req_id: int, body: ReviewIn, sub_account_id: int | None = None, x_admin_password: str = Header(default="")):
    """审核申请，可同时分配子账号。"""
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        if sub_account_id is not None:
            conn.execute(
                "UPDATE api_requests SET status=?,review_note=?,sub_account_id=?,updated_at=? WHERE id=?",
                (body.status, body.review_note, sub_account_id, now, req_id)
            )
        else:
            conn.execute(
                "UPDATE api_requests SET status=?,review_note=?,updated_at=? WHERE id=?",
                (body.status, body.review_note, now, req_id)
            )
        conn.commit()
    return {"ok": True}

@app.get("/api-requests/approved")
def approved_configs(x_token: str = Header(default="")):
    """返回当前用户已审核通过的 API 配置列表，含子账号信息。"""
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.provider, c.base_url, c.created_at, c.updated_at,
                   r.id as request_id, r.sub_account_id,
                   sa.name as sub_account_name, sa.available_models as sub_models,
                   sa.quota_type, sa.quota_amount, sa.ip_restriction
            FROM api_requests r
            JOIN api_configs c ON c.id = r.config_id
            LEFT JOIN sub_accounts sa ON sa.id = r.sub_account_id
            WHERE r.user_id=? AND r.status='approved' AND c.is_active=1
            ORDER BY c.name
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]

# ── SQLite 批量历史 ────────────────────────────────────────

class BatchJobIn(BaseModel):
    task_name: str
    model: str = ""
    config_id: int | None = None
    label: str = ""
    row_count: int = 0

class BatchJobRowIn(BaseModel):
    job_id: int
    row_index: int
    input_json: str
    output_text: str = ""
    success: int = 1
    error_msg: str = ""

@app.post("/batch2/jobs")
def create_batch_job(body: BatchJobIn, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO batch_jobs (user_id,task_name,model,config_id,label,row_count,created_at) VALUES (?,?,?,?,?,?,?)",
            (user["id"], body.task_name, body.model, body.config_id, body.label, body.row_count, now)
        )
        conn.commit()
    return {"job_id": cur.lastrowid}

@app.post("/batch2/rows")
def save_batch_row(body: BatchJobRowIn, x_token: str = Header(default="")):
    get_current_user(x_token)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO batch_job_rows (job_id,row_index,input_json,output_text,success,error_msg) VALUES (?,?,?,?,?,?)",
            (body.job_id, body.row_index, body.input_json, body.output_text, body.success, body.error_msg)
        )
        if body.success:
            conn.execute("UPDATE batch_jobs SET done_count=done_count+1 WHERE id=?", (body.job_id,))
        else:
            conn.execute("UPDATE batch_jobs SET fail_count=fail_count+1 WHERE id=?", (body.job_id,))
        conn.commit()
    return {"ok": True}

@app.put("/batch2/jobs/{job_id}/finish")
def finish_batch_job(job_id: int, status: str = "completed", x_token: str = Header(default="")):
    get_current_user(x_token)
    with get_db() as conn:
        conn.execute("UPDATE batch_jobs SET status=? WHERE id=?", (status, job_id))
        conn.commit()
    return {"ok": True}

@app.get("/batch2/jobs")
def list_batch_jobs(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
            (user["id"],)
        ).fetchall()
    return [dict(r) for r in rows]

@app.get("/batch2/jobs/{job_id}/rows")
def get_batch_job_rows(job_id: int, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        job = conn.execute("SELECT user_id FROM batch_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="无权限")
        rows = conn.execute(
            "SELECT * FROM batch_job_rows WHERE job_id=? ORDER BY row_index",
            (job_id,)
        ).fetchall()
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

# ── 脚本编辑器 ────────────────────────────────────────────

import tempfile, threading

SCRIPT_BASE_DIR = os.path.join(tempfile.gettempdir(), "script_runs")
os.makedirs(SCRIPT_BASE_DIR, exist_ok=True)
SCRIPT_WORKER   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "script_worker.py")
SCRIPT_TIMEOUT  = 30
SCRIPT_MAX_SIZE = 1024 ** 3  # 1 GB

def _check_approved(user_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM api_requests WHERE user_id=? AND status='approved' LIMIT 1",
            (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="需要已审批的 API 权限才能运行脚本")

@app.post("/script/upload")
async def script_upload(file: UploadFile, x_token: str = Header(default="")):
    user = get_current_user(x_token)
    _check_approved(user["id"])

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in ('.parquet', '.json', '.csv', '.xlsx', '.xls'):
        raise HTTPException(status_code=400, detail="仅支持 parquet / json / csv / xlsx")

    content = await file.read()
    if len(content) > SCRIPT_MAX_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 1GB 上限")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=SCRIPT_BASE_DIR)
    tmp.write(content); tmp.close()

    try:
        import pandas as _pd
        loaders = {
            '.parquet': _pd.read_parquet,
            '.json':    _pd.read_json,
            '.xlsx':    _pd.read_excel,
            '.xls':     _pd.read_excel,
            '.csv':     _pd.read_csv,
        }
        df = loaders[suffix](tmp.name)
        rows, cols = df.shape
        col_names  = df.columns.tolist()
    except Exception:
        rows, cols, col_names = 0, 0, []

    return {"file_id": tmp.name, "filename": file.filename,
            "rows": rows, "columns": cols, "col_names": col_names}


@app.post("/script/run")
async def script_run(
    code:      str      = Body(...),
    config_id: int|None = Body(None),
    model:     str      = Body(""),
    file_id:   str|None = Body(None),
    x_token:   str      = Header(default="")
):
    user = get_current_user(x_token)
    _check_approved(user["id"])

    run_id   = str(uuid.uuid4())
    work_dir = os.path.join(SCRIPT_BASE_DIR, run_id)
    os.makedirs(work_dir, exist_ok=True)

    script_path = os.path.join(work_dir, "user_script.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)

    cmd = [
        sys.executable, SCRIPT_WORKER,
        "--run-id",    run_id,
        "--script",    script_path,
        "--work-dir",  work_dir,
        "--token",     x_token,
        "--config-id", str(config_id) if config_id else "",
        "--model",     model or "",
        "--file-path", file_id or "",
        "--timeout",   str(SCRIPT_TIMEOUT),
    ]

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=SCRIPT_TIMEOUT + 10
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    yield f"data: {json.dumps({'type':'error','text':'Worker 无响应，已强制停止'})}\n\n"
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    yield f"data: {text}\n\n"
            # drain stderr（worker 自身错误）
            try:
                err = await asyncio.wait_for(proc.stderr.read(), timeout=3)
                if err:
                    msg = err.decode("utf-8", errors="replace").strip()
                    if msg:
                        yield f"data: {json.dumps({'type':'error','text':msg})}\n\n"
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            try: proc.kill()
            except Exception: pass
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"
        finally:
            try: await proc.wait()
            except Exception: pass

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/script/result/{run_id}/download")
def script_download(run_id: str, x_token: str = Header(default="")):
    get_current_user(x_token)
    work_dir = os.path.join(SCRIPT_BASE_DIR, run_id)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="运行结果不存在或已过期")

    import io, zipfile
    from fastapi.responses import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(work_dir):
            fpath = os.path.join(work_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith("_") and fname != "user_script.py":
                zf.write(fpath, fname)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=result_{run_id[:8]}.zip"}
    )


# ── 后台清理（每小时删除 24h 前的运行目录）────────────────
def _cleanup_old_runs():
    import shutil
    cutoff = time.time() - 86400
    try:
        for name in os.listdir(SCRIPT_BASE_DIR):
            path = os.path.join(SCRIPT_BASE_DIR, name)
            if os.path.isdir(path):
                try:
                    if os.path.getmtime(path) < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass

def _start_cleanup_thread():
    def loop():
        while True:
            time.sleep(3600)
            _cleanup_old_runs()
    t = threading.Thread(target=loop, daemon=True)
    t.start()

_start_cleanup_thread()

# ── 静态文件服务（让前端可通过 http://localhost:8000/ 访问）──
_ROOT = os.path.dirname(os.path.abspath(__file__))
app.mount("/assets", StaticFiles(directory=os.path.join(_ROOT, "assets")), name="assets")
app.mount("/pages",  StaticFiles(directory=os.path.join(_ROOT, "pages")),  name="pages")
app.mount("/config", StaticFiles(directory=os.path.join(_ROOT, "config")), name="config")
if os.path.isdir(os.path.join(_ROOT, "src")):
    app.mount("/src", StaticFiles(directory=os.path.join(_ROOT, "src")), name="src")

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(_ROOT, "index.html"))

@app.get("/index.html")
def serve_index_explicit():
    return FileResponse(os.path.join(_ROOT, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
