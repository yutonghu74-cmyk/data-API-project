# Claude API Chat 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 api-web-project 中新增 Claude API 流式聊天页，含 FastAPI 代理后端和完整聊天前端。

**Architecture:** 浏览器通过 fetch 调用本地 FastAPI 代理（:8000），代理用 Anthropic SDK stream() 将 SSE 流透传给浏览器，前端 stream.js 读取 ReadableStream 逐 token 渲染。

**Tech Stack:** Python 3.9+, FastAPI, uvicorn, anthropic SDK, 原生 ES Modules（无构建工具）

---

## 文件映射

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `requirements.txt` | Python 依赖声明 |
| 新建 | `server.py` | FastAPI 代理，3 个端点，SSE 透传 |
| 新建 | `assets/js/stream.js` | 前端 SSE 流读取封装 |
| 新建 | `pages/claude.html` | 完整聊天页面 |
| 修改 | `index.html` | 注册 Claude Chat 导航卡片 |

---

### Task 1: 创建 requirements.txt 并安装依赖

**Files:**
- 新建: `requirements.txt`

- [ ] **Step 1: 写入依赖文件**

```
anthropic>=0.40.0
fastapi>=0.115.0
uvicorn>=0.32.0
python-dotenv>=1.0.0
```

- [ ] **Step 2: 安装依赖**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
pip install -r requirements.txt
```

预期输出：`Successfully installed anthropic-... fastapi-... uvicorn-...`

- [ ] **Step 3: 验证安装**

```bash
python -c "import anthropic, fastapi, uvicorn; print('OK')"
```

预期输出：`OK`

- [ ] **Step 4: 提交**

```bash
git add requirements.txt
git commit -m "feat: add Python dependencies for Claude API proxy"
```

---

### Task 2: 创建 FastAPI 代理 server.py

**Files:**
- 新建: `server.py`

- [ ] **Step 1: 写入 server.py**

```python
import os
import sys
import json
import asyncio
import anthropic

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7"]

class ChatRequest(BaseModel):
    messages: list[dict]
    model: str = "claude-sonnet-4-6"
    system: str = ""

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/models")
def models():
    return {"models": MODELS}

@app.post("/chat")
def chat(req: ChatRequest):
    if req.model not in MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {req.model}")

    def generate():
        try:
            kwargs = dict(
                model=req.model,
                max_tokens=4096,
                messages=req.messages,
            )
            if req.system:
                kwargs["system"] = req.system

            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
            yield "data: [DONE]\n\n"
        except anthropic.APIError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

- [ ] **Step 2: 启动服务器验证语法无误**

```bash
export ANTHROPIC_API_KEY=your-key-here
python server.py &
sleep 2
```

- [ ] **Step 3: 测试 /health 端点**

```bash
curl http://localhost:8000/health
```

预期输出：`{"status":"ok"}`

- [ ] **Step 4: 测试 /models 端点**

```bash
curl http://localhost:8000/models
```

预期输出：`{"models":["claude-haiku-4-5-20251001","claude-sonnet-4-6","claude-opus-4-7"]}`

- [ ] **Step 5: 停止后台服务器**

```bash
kill %1
```

- [ ] **Step 6: 提交**

```bash
git add server.py
git commit -m "feat: add FastAPI SSE proxy for Claude API"
```

---

### Task 3: 创建 stream.js 前端封装

**Files:**
- 新建: `assets/js/stream.js`

- [ ] **Step 1: 写入 stream.js**

```js
const BASE_URL = 'http://localhost:8000';

export async function streamChat({ messages, model, system = '', onToken, onDone, onError }) {
  let res;
  try {
    res = await fetch(`${BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, model, system }),
    });
  } catch {
    onError('无法连接到代理服务，请先启动 server.py（python server.py）');
    return;
  }

  if (!res.ok) {
    onError(`服务器错误 ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6).trim();
      if (raw === '[DONE]') { onDone(); return; }

      try {
        const msg = JSON.parse(raw);
        if (msg.error) { onError(msg.error); return; }
        if (msg.text)  { onToken(msg.text); }
      } catch { /* ignore malformed lines */ }
    }
  }

  onDone();
}

export async function fetchModels() {
  const res = await fetch(`${BASE_URL}/models`);
  const data = await res.json();
  return data.models;
}
```

- [ ] **Step 2: 验证文件无语法错误**

```bash
node --input-type=module < assets/js/stream.js 2>&1 | head -5
```

预期：无输出（无报错）

- [ ] **Step 3: 提交**

```bash
git add assets/js/stream.js
git commit -m "feat: add SSE stream.js client for Claude API"
```

---

### Task 4: 创建聊天页面 pages/claude.html

**Files:**
- 新建: `pages/claude.html`

- [ ] **Step 1: 写入 pages/claude.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Claude Chat</title>
  <link rel="stylesheet" href="../assets/css/base.css">
  <style>
    .page-header {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 20px 0 28px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }
    .page-header a { color: var(--text-muted); text-decoration: none; font-size: .9rem; }
    .page-header a:hover { color: var(--text); }
    .page-header h1 { font-size: 1.1rem; font-weight: 600; }

    .config-card { margin-bottom: 16px; display: grid; gap: 14px; }
    .config-row { display: grid; grid-template-columns: 1fr 200px; gap: 14px; }

    .messages {
      height: 420px;
      overflow-y: auto;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 16px;
    }

    .bubble {
      max-width: 80%;
      padding: 10px 14px;
      border-radius: 12px;
      font-size: .92rem;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .bubble-user {
      background: var(--accent);
      color: #fff;
      align-self: flex-end;
      border-bottom-right-radius: 4px;
    }
    .bubble-assistant {
      background: var(--surface);
      border: 1px solid var(--border);
      align-self: flex-start;
      border-bottom-left-radius: 4px;
    }
    .bubble-error {
      background: rgba(248,113,113,.1);
      border: 1px solid var(--error);
      color: var(--error);
      align-self: flex-start;
      border-radius: var(--radius);
      font-size: .85rem;
    }
    .cursor::after {
      content: '▋';
      animation: blink .7s step-end infinite;
    }
    @keyframes blink { 50% { opacity: 0; } }

    .input-row {
      display: flex;
      gap: 10px;
      align-items: flex-end;
    }
    .input-row textarea {
      flex: 1;
      resize: none;
      height: 44px;
      line-height: 1.5;
      padding: 10px 14px;
    }
    .btn-secondary {
      background: var(--border);
      color: var(--text);
    }
    .btn-secondary:hover { background: var(--surface); }

    .empty-hint {
      color: var(--text-muted);
      font-size: .85rem;
      text-align: center;
      margin: auto;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="page-header">
      <a href="../index.html">← 返回</a>
      <h1>Claude Chat</h1>
    </div>

    <div class="card config-card">
      <div class="form-group" style="margin:0">
        <label>System Prompt</label>
        <textarea id="systemPrompt" rows="2" placeholder="可选：设置助手角色或背景…" style="resize:vertical"></textarea>
      </div>
      <div class="config-row">
        <div class="form-group" style="margin:0">
          <label>Model</label>
          <select id="modelSelect"></select>
        </div>
      </div>
    </div>

    <div class="messages" id="messages">
      <p class="empty-hint">发送消息开始对话</p>
    </div>

    <div class="input-row">
      <textarea id="userInput" placeholder="输入消息… (Enter 发送，Shift+Enter 换行)"></textarea>
      <button class="btn-primary" id="sendBtn">发送</button>
      <button class="btn-secondary" id="clearBtn">清空</button>
    </div>
  </div>

  <script type="module">
    import { streamChat, fetchModels } from '../assets/js/stream.js';

    const messagesEl  = document.getElementById('messages');
    const userInput   = document.getElementById('userInput');
    const sendBtn     = document.getElementById('sendBtn');
    const clearBtn    = document.getElementById('clearBtn');
    const modelSelect = document.getElementById('modelSelect');
    const systemPrompt = document.getElementById('systemPrompt');

    let history = [];

    // 加载模型列表
    fetchModels()
      .then(models => {
        models.forEach(m => {
          const opt = document.createElement('option');
          opt.value = m;
          opt.textContent = m;
          modelSelect.appendChild(opt);
        });
        // 默认选 Sonnet
        const sonnetOpt = [...modelSelect.options].find(o => o.value.includes('sonnet'));
        if (sonnetOpt) sonnetOpt.selected = true;
      })
      .catch(() => {
        ['claude-haiku-4-5-20251001','claude-sonnet-4-6','claude-opus-4-7'].forEach(m => {
          const opt = document.createElement('option');
          opt.value = m; opt.textContent = m;
          modelSelect.appendChild(opt);
        });
      });

    function addBubble(role, text = '') {
      const empty = messagesEl.querySelector('.empty-hint');
      if (empty) empty.remove();

      const div = document.createElement('div');
      div.className = `bubble bubble-${role}`;
      div.textContent = text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    function addError(msg) {
      const div = document.createElement('div');
      div.className = 'bubble bubble-error';
      div.textContent = msg;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function setInputEnabled(enabled) {
      userInput.disabled = !enabled;
      sendBtn.disabled   = !enabled;
    }

    async function send() {
      const text = userInput.value.trim();
      if (!text) return;

      userInput.value = '';
      addBubble('user', text);
      history.push({ role: 'user', content: text });

      setInputEnabled(false);
      const assistantBubble = addBubble('assistant');
      assistantBubble.classList.add('cursor');
      let accumulated = '';

      await streamChat({
        messages: history,
        model:    modelSelect.value,
        system:   systemPrompt.value.trim(),
        onToken(chunk) {
          accumulated += chunk;
          assistantBubble.textContent = accumulated;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        },
        onDone() {
          assistantBubble.classList.remove('cursor');
          history.push({ role: 'assistant', content: accumulated });
          setInputEnabled(true);
          userInput.focus();
        },
        onError(err) {
          assistantBubble.remove();
          history.pop();
          addError(err);
          setInputEnabled(true);
        },
      });
    }

    sendBtn.addEventListener('click', send);

    userInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });

    clearBtn.addEventListener('click', () => {
      history = [];
      messagesEl.innerHTML = '<p class="empty-hint">发送消息开始对话</p>';
    });
  </script>
</body>
</html>
```

- [ ] **Step 2: 验证文件已创建**

```bash
ls -lh pages/claude.html
```

预期：显示文件大小 > 3KB

- [ ] **Step 3: 提交**

```bash
git add pages/claude.html
git commit -m "feat: add Claude Chat page with streaming UI"
```

---

### Task 5: 注册导航卡片并端到端验证

**Files:**
- 修改: `index.html`（pages 数组）

- [ ] **Step 1: 在 index.html 的 pages 数组中添加 Claude Chat 卡片**

找到 `index.html` 第 74 行的 `const pages = [` 数组，替换为：

```js
const pages = [
  { icon: '🤖', title: 'Claude Chat', desc: '流式对话 · 多轮历史', href: 'pages/claude.html' },
];
```

- [ ] **Step 2: 启动后端**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python server.py &
sleep 2
curl http://localhost:8000/health
```

预期：`{"status":"ok"}`

- [ ] **Step 3: 浏览器打开首页验证卡片出现**

用浏览器打开 `index.html`，确认首页显示「Claude Chat」导航卡片。

- [ ] **Step 4: 点击卡片进入聊天页，发送一条消息**

验证：
- 模型下拉框有 3 个选项
- 发送后出现 user 气泡
- assistant 气泡出现光标动画
- 文字逐字流式渲染
- 完成后光标消失，输入框恢复可用

- [ ] **Step 5: 测试错误场景——停止后端后发送消息**

```bash
kill %1
```

在页面发送消息，应看到红色错误气泡：「无法连接到代理服务，请先启动 server.py」

- [ ] **Step 6: 测试清空**

点击「清空」按钮，对话历史清除，System Prompt 和模型选择保留。

- [ ] **Step 7: 提交**

```bash
git add index.html
git commit -m "feat: register Claude Chat in homepage navigation"
```
