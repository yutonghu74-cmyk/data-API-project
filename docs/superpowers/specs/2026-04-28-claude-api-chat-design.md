# Claude API Chat — 设计规格

**日期：** 2026-04-28  
**状态：** 已批准

---

## 目标

在 `api-web-project` 中新增一个 Claude API 异步聊天页面，支持流式输出、多轮对话、模型选择和 System Prompt 自定义。

---

## 架构

```
浏览器 (pages/claude.html)
    │  fetch POST /chat  { messages, model, system }
    ▼
FastAPI 代理 (server.py，本地 :8000)
    │  anthropic SDK stream()
    ▼
Anthropic API (api.anthropic.com)
    │  SSE text_delta chunks
    ▼  透传 StreamingResponse
浏览器 ReadableStream → 逐 token 渲染
```

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `server.py` | FastAPI 代理服务 |
| `requirements.txt` | Python 依赖：anthropic, fastapi, uvicorn |
| `pages/claude.html` | 完整版聊天页面 |
| `assets/js/stream.js` | SSE 流读取封装（不修改 request.js） |

---

## 后端设计（server.py）

**端点：**

- `POST /chat` — 接收 `{ messages, model, system }`，返回 `text/event-stream` SSE 流
- `GET /models` — 返回可用模型列表 `["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"]`
- `GET /health` — 健康检查，返回 `{ status: "ok" }`

**关键实现：**
- 使用 `anthropic` SDK 的 `client.messages.stream()` 上下文管理器
- `StreamingResponse(generator, media_type="text/event-stream")` 透传给浏览器
- API Key 从环境变量 `ANTHROPIC_API_KEY` 读取，启动时若缺失则报错退出
- CORS 配置允许 `http://localhost:*` 和 `null`（file:// 协议）

---

## 前端设计（pages/claude.html）

**页面布局：**

```
┌─────────────────────────────────────┐
│ ← 返回  │  Claude Chat              │
├─────────────────────────────────────┤
│ System Prompt  [textarea]           │
│ Model  [Haiku ▼ / Sonnet / Opus]   │
├─────────────────────────────────────┤
│  对话气泡区（可滚动）                │
│  user / assistant 气泡交替显示       │
│  assistant 流式逐字渲染              │
├─────────────────────────────────────┤
│ [输入框（Enter 发送）]  [发送][清空] │
└─────────────────────────────────────┘
```

**交互行为：**
- 发送中：禁用输入框和发送按钮，assistant 气泡显示光标闪烁动画
- 流结束后：自动滚动到底部，恢复输入
- 清空：清除对话历史，保留 System Prompt 和模型选择
- 错误：在气泡区内以红色错误气泡展示，不中断页面

**stream.js 接口：**
```js
streamChat({ messages, model, system, onToken, onDone, onError })
// onToken(text) — 每个 token 回调
// onDone()      — 流结束回调
// onError(err)  — 错误回调
```

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 后端未启动 | 显示"无法连接到代理服务，请先启动 server.py" |
| API Key 无效 | 显示 Anthropic 返回的错误信息 |
| 网络中断 | 流中断，显示已接收内容 + 错误提示 |
| 空消息发送 | 前端拦截，不发送请求 |

---

## 启动方式

```bash
cd api-web-project
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python server.py
# 然后用浏览器打开 pages/claude.html
```
