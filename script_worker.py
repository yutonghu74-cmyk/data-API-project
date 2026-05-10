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
parser.add_argument('--config-id', default='')
parser.add_argument('--model',     default='')
parser.add_argument('--file-path', default='')
parser.add_argument('--timeout',   type=int, default=30)
args = parser.parse_args()

def emit(obj):
    """输出一个 JSON 行到 stdout，FastAPI 读取后转为 SSE event。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

# ── Bootstrap 代码（注入到用户脚本前）────────────────────
def build_bootstrap() -> str:
    config_id_val = args.config_id if args.config_id else 'None'

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

# ── 安全拦截 ──────────────────────────────────────────────
_BLOCKED = {{"os","subprocess","socket","pty","ctypes","shutil","importlib"}}
_orig_import = _b.__import__
def _safe_import(name, *a, **kw):
    top = name.split(".")[0]
    if top in _BLOCKED:
        raise ImportError(f"禁止导入: {{name}}")
    return _orig_import(name, *a, **kw)
_b.__import__ = _safe_import

# ── 注入变量 ──────────────────────────────────────────────
WORK_DIR  = r"{args.work_dir}"
AI_MODEL  = "{args.model}"
_TOKEN    = "{args.token}"
_CFG_ID   = {config_id_val}

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
def chat(prompt, model=None):
    """调 AI 代理，同步阻塞，返回完整文本。"""
    payload = _json.dumps({{
        "messages": [{{"role": "user", "content": str(prompt)}}],
        "model": model or AI_MODEL,
        "system": "",
        "config_id": _CFG_ID,
        "user_token": _TOKEN,
    }}).encode()
    conn = _hc.HTTPConnection("localhost", 8000, timeout=120)
    conn.request("POST", "/chat", body=payload,
                 headers={{"Content-Type": "application/json", "X-Token": _TOKEN}})
    resp = conn.getresponse()
    result = ""
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
                except Exception:
                    pass
    conn.close()
    return result

def show_image(path):
    with open(path, "rb") as f:
        data = _b64.b64encode(f.read()).decode()
    ext = _pl.Path(path).suffix.lstrip(".") or "png"
    mime = {{"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}}.get(ext, "png")
    print(f"__IMG__data:image/{{mime}};base64,{{data}}", flush=True)

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

# ── 合并脚本 ──────────────────────────────────────────────
user_code = open(args.script, encoding='utf-8').read()
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
