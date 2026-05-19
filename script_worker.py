#!/usr/bin/env python3
"""
script_worker.py — 独立执行器
由 server.py spawn，stdout 输出 JSON 行供 FastAPI 转发为 SSE。

用法：
  python script_worker.py \
    --run-id <uuid> \
    --script <path/to/user_script.py> \
    --work-dir <path> \
    --token <user_token> \
    --config-id <int|""> \
    --model <model_name> \
    --file-path <path|""> \
    --timeout <seconds>
"""
import argparse, sys, os, json, time, select
import subprocess, base64, pathlib, tempfile

# ── 参数解析 ──────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--run-id',    required=True)
parser.add_argument('--script',    required=True)
parser.add_argument('--work-dir',  required=True)
parser.add_argument('--token',     required=True)
parser.add_argument('--config-id',  default='')
parser.add_argument('--request-id', default='')
parser.add_argument('--job-id',     default='')
parser.add_argument('--resume-done-csv', default='')
parser.add_argument('--model',     default='')
parser.add_argument('--file-path', default='')
parser.add_argument('--timeout',   type=int, default=30)
args = parser.parse_args()

def emit(obj):
    """输出一个 JSON 行到 stdout，FastAPI 读取后转为 SSE event。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

# ── Bootstrap 代码（注入到用户脚本前）────────────────────
def build_bootstrap() -> str:
    config_id_val  = args.config_id  if args.config_id  else 'None'
    request_id_val = args.request_id if args.request_id else 'None'
    job_id_val     = args.job_id     if args.job_id     else 'None'
    resume_done_repr = "set()"
    if args.resume_done_csv:
        try:
            ids = [int(x) for x in args.resume_done_csv.split(",") if x.strip()]
            resume_done_repr = "{" + ", ".join(str(i) for i in ids) + "}" if ids else "set()"
        except Exception:
            resume_done_repr = "set()"

    if args.file_path and os.path.exists(args.file_path):
        ext = pathlib.Path(args.file_path).suffix.lower()
        read_map = {
            '.parquet': f"df = _pd.read_parquet(r'{args.file_path}')",
            '.json':    f"df = _pd.read_json(r'{args.file_path}')",
            '.xlsx':    f"df = _pd.read_excel(r'{args.file_path}')",
            '.xls':     f"df = _pd.read_excel(r'{args.file_path}')",
            '.csv':     f"df = _pd.read_csv(r'{args.file_path}')",
        }
        load_df = read_map.get(ext, f"df = _pd.read_csv(r'{args.file_path}')")
    else:
        load_df = "df = _pd.DataFrame()"

    return f'''
import builtins as _b, sys as _sys, os as _os
import json as _json, base64 as _b64, pandas as _pd
import pathlib as _pl, http.client as _hc, io as _io

# （无运行时 import hook：已在 worker 启动前做 AST 静态检查）

# ── 注入变量 ──────────────────────────────────────────────
WORK_DIR  = r"{args.work_dir}"
AI_MODEL  = "{args.model}"
_TOKEN    = "{args.token}"
_CFG_ID   = {config_id_val}
_REQ_ID   = {request_id_val}
_JOB_ID   = {job_id_val}
_RESUME_DONE = {resume_done_repr}

# 安全 os 代理（只暴露文件路径操作，禁止 system/exec）
class _SafeOS:
    path    = _os.path
    sep     = _os.sep
    getcwd  = staticmethod(_os.getcwd)
    listdir = staticmethod(_os.listdir)
    makedirs= staticmethod(_os.makedirs)
    environ = {{}}
    def join(self, *a): return _os.path.join(*a)
    def path_join(self, *a): return _os.path.join(*a)
os = _SafeOS()

{load_df}

# ── 注入函数 ──────────────────────────────────────────────
def _to_image_block(path):
    """把本地图片路径转 Anthropic image content block。失败返回 None。"""
    try:
        with open(path, "rb") as f:
            data = _b64.b64encode(f.read()).decode()
        ext = _pl.Path(path).suffix.lstrip(".").lower() or "png"
        mime = {{"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}}.get(ext, "png")
        return {{"type": "image", "source": {{"type": "base64", "media_type": f"image/{{mime}}", "data": data}}}}
    except Exception:
        return None

def _is_image_path(s):
    if not isinstance(s, str): return False
    if len(s) > 1024: return False
    low = s.strip().lower()
    if not low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")): return False
    return _os.path.exists(s.strip())

def chat(prompt, model=None):
    """调 AI 代理。prompt 支持：
       - str：纯文本
       - list[str|dict]：自动把图片路径或 dict({{"image": "/path"}}) 转成 image block
    """
    if isinstance(prompt, list):
        blocks = []
        for item in prompt:
            if isinstance(item, dict) and item.get("image"):
                blk = _to_image_block(item["image"])
                if blk: blocks.append(blk)
                else: blocks.append({{"type": "text", "text": f"[图片读取失败: {{item['image']}}]"}})
            elif isinstance(item, str) and _is_image_path(item):
                blk = _to_image_block(item)
                if blk: blocks.append(blk)
                else: blocks.append({{"type": "text", "text": item}})
            else:
                blocks.append({{"type": "text", "text": str(item)}})
        content = blocks
    else:
        content = str(prompt)
    payload = _json.dumps({{
        "messages": [{{"role": "user", "content": content}}],
        "model": model or AI_MODEL,
        "system": "",
        "config_id": _CFG_ID,
        "request_id": _REQ_ID,
        "user_token": _TOKEN,
    }}).encode()
    conn = _hc.HTTPConnection("localhost", 8000, timeout=120)
    conn.request("POST", "/chat", body=payload,
                 headers={{"Content-Type": "application/json", "X-Token": _TOKEN}})
    resp = conn.getresponse()
    result = ""
    err_msg = None
    buf = b""
    while True:
        chunk = resp.read(512)
        if not chunk:
            break
        buf += chunk
        while b"\\n" in buf:
            line, buf = buf.split(b"\\n", 1)
            line = line.decode("utf-8", errors="replace").strip()
            if line.startswith("data: "):
                raw = line[6:]
                if raw == "[DONE]":
                    break
                try:
                    msg = _json.loads(raw)
                    if msg.get("text"):
                        result += msg["text"]
                    elif msg.get("error"):
                        err_msg = msg["error"]
                except Exception:
                    pass
    conn.close()
    if err_msg and not result:
        raise RuntimeError(err_msg)
    return result

def show_image(path):
    with open(path, "rb") as f:
        data = _b64.b64encode(f.read()).decode()
    ext = _pl.Path(path).suffix.lstrip(".") or "png"
    mime = {{"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}}.get(ext, "png")
    print(f"__IMG__data:image/{{mime}};base64,{{data}}", flush=True)

def record_row(idx, input=None, output="", success=True, error="", started_at=None, finished_at=None):
    """记录单行处理结果。若 _JOB_ID 已知,直接写入 batch_job_rows;否则仅 emit 事件供 SSE。"""
    from datetime import datetime as _dt, timezone as _tz
    import math as _math
    def _clean_nan(o):
        if isinstance(o, float) and (_math.isnan(o) or _math.isinf(o)): return None
        if isinstance(o, dict): return {{k: _clean_nan(v) for k, v in o.items()}}
        if isinstance(o, (list, tuple)): return [_clean_nan(x) for x in o]
        return o
    now_iso = _dt.now(_tz.utc).isoformat()
    if started_at is None: started_at = now_iso
    if finished_at is None: finished_at = now_iso
    payload = {{
        "idx": int(idx) if isinstance(idx, (int, float)) else 0,
        "input": _clean_nan(input) if input is not None else {{}},
        "output": str(output) if output is not None else "",
        "success": 1 if success else 0,
        "error": str(error) if error else "",
        "started_at": started_at,
        "finished_at": finished_at,
    }}
    if _JOB_ID is not None:
        try:
            body = _json.dumps({{
                "job_id": _JOB_ID, "row_index": payload["idx"],
                "input_json": _json.dumps(payload["input"], ensure_ascii=False, default=str),
                "output_text": payload["output"], "output_type": "text", "output_path": "",
                "label": "", "success": payload["success"], "error_msg": payload["error"],
                "started_at": payload["started_at"], "finished_at": payload["finished_at"],
            }}).encode()
            conn = _hc.HTTPConnection("localhost", 8000, timeout=15)
            conn.request("POST", "/batch2/rows", body=body,
                         headers={{"Content-Type":"application/json","X-Token":_TOKEN}})
            conn.getresponse().read(); conn.close()
        except Exception:
            pass
    print(f"__ROW__{{_json.dumps(payload, ensure_ascii=False, default=str)}}", flush=True)

def show_chart(fig):
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    data = _b64.b64encode(buf.read()).decode()
    print(f"__IMG__data:image/png;base64,{{data}}", flush=True)

def show_video(path):
    print(f"__VID__{{path}}", flush=True)

def save_file(path):
    size = _os.path.getsize(path) if _os.path.exists(path) else 0
    name = _pl.Path(path).name
    print(f"__FILE__{{name}}|{{size}}", flush=True)

# ── 用户代码开始 ───────────────────────────────────────────
'''

# ── AST 静态安全检查 ──────────────────────────────────────
import ast as _ast

BLOCKED_IMPORTS = {"subprocess", "socket", "pty", "ctypes"}

def _check_user_code(code: str):
    """解析用户代码，检测禁止导入。"""
    try:
        tree = _ast.parse(code)
    except SyntaxError as e:
        return f"脚本语法错误：{e}"
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            names = [n.name for n in node.names] if isinstance(node, _ast.Import) else [node.module or ""]
            for name in names:
                top = (name or "").split(".")[0]
                if top in BLOCKED_IMPORTS:
                    return f"禁止导入: {name}"
    return None

user_code = open(args.script, encoding='utf-8').read()

# 检查安全性
_err = _check_user_code(user_code)
if _err:
    emit({"type": "error", "text": _err + "\n"})
    emit({"type": "done", "run_id": args.run_id, "file_count": 0, "elapsed": 0.0, "returncode": 1})
    sys.exit(1)

# ── 合并脚本 ──────────────────────────────────────────────
full_code  = build_bootstrap() + "\n" + user_code

combined_path = os.path.join(args.work_dir, "_combined.py")
with open(combined_path, 'w', encoding='utf-8') as f:
    f.write(full_code)

# ── 执行子进程 ────────────────────────────────────────────
emit({"type": "info", "text": f"▶ 脚本启动（timeout={args.timeout}s）\n"})
start = time.time()
file_count = 0

try:
    proc = subprocess.Popen(
        [sys.executable, combined_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace',
        cwd=args.work_dir,
    )
except Exception as e:
    emit({"type": "error", "text": f"启动失败: {e}\n"})
    sys.exit(1)

# ── 读取输出（select 非阻塞）─────────────────────────────
def process_line(line: str, is_err: bool):
    global file_count
    line = line.rstrip('\n')
    if is_err:
        emit({"type": "stderr", "text": line + "\n"})
        return
    if line.startswith("__IMG__"):
        emit({"type": "image", "data": line[7:]})
    elif line.startswith("__VID__"):
        fname = os.path.basename(line[7:])
        emit({"type": "video", "run_id": args.run_id, "filename": fname})
    elif line.startswith("__FILE__"):
        parts = line[8:].split("|", 1)
        name = parts[0]
        size = int(parts[1]) if len(parts) > 1 else 0
        file_count += 1
        emit({"type": "file", "run_id": args.run_id, "name": name, "size": size})
    elif line.startswith("__ROW__"):
        try:
            data = json.loads(line[7:])
        except Exception:
            data = {}
        emit({"type": "row", **data})
    else:
        emit({"type": "stdout", "text": line + "\n"})

timed_out = False
try:
    while True:
        elapsed = time.time() - start
        if elapsed >= args.timeout:
            proc.kill()
            timed_out = True
            break

        remaining = args.timeout - elapsed
        rlist, _, _ = select.select(
            [proc.stdout, proc.stderr], [], [], min(0.1, remaining)
        )
        for stream in rlist:
            line = stream.readline()
            if line:
                process_line(line, stream is proc.stderr)

        if proc.poll() is not None:
            for line in proc.stdout: process_line(line, False)
            for line in proc.stderr: process_line(line, True)
            break
except KeyboardInterrupt:
    proc.kill()

if timed_out:
    emit({"type": "error", "text": f"超时（{args.timeout}秒）已自动停止\n"})

rc = proc.wait() if not timed_out else -1
elapsed = round(time.time() - start, 1)
emit({"type": "done", "run_id": args.run_id,
      "file_count": file_count, "elapsed": elapsed,
      "returncode": rc})
