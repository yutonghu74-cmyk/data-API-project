import os
import sys
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import Header
from fastapi.responses import JSONResponse

DB_PATH = os.path.join(os.path.dirname(__file__), "admin.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # 建议在 .env 中设置强密码

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
    import hmac
    if not hmac.compare_digest(x_admin_password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "null"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7"]

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
    d["api_key"] = "****" + d["api_key"][-4:] if len(d["api_key"]) >= 4 else "****"
    return d

@app.put("/admin/configs/{config_id}")
def update_config(config_id: int, body: ConfigIn, x_admin_password: str = Header(default="")):
    require_admin(x_admin_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        if body.api_key and body.api_key != "(unchanged)":
            conn.execute(
                "UPDATE api_configs SET name=?,base_url=?,api_key=?,provider=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.api_key, body.provider, now, body.is_active, config_id)
            )
        else:
            conn.execute(
                "UPDATE api_configs SET name=?,base_url=?,provider=?,updated_at=?,is_active=? WHERE id=?",
                (body.name, body.base_url, body.provider, now, body.is_active, config_id)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
