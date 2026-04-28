# 文件上传功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Claude Chat 页面添加多文件上传（图片+文档），通过 base64 内联方式发送给 Claude API，支持点击选择和拖拽两种方式。

**Architecture:** 前端读取文件转 base64 存入 `attachments` 数组，发送时构建多模态 content 数组；后端 `Message.content` 改为支持 `str | list[block]` 联合类型，Anthropic SDK 原生序列化。

**Tech Stack:** 原生 ES Modules（无框架），Python FastAPI + Pydantic，Anthropic SDK

---

## 文件映射

| 操作 | 文件 | 改动内容 |
|------|------|---------|
| 修改 | `server.py` | 新增 TextBlock/ImageBlock/DocumentBlock，Message.content 改为联合类型 |
| 修改 | `pages/claude.html` | 新增附件 UI、base64 读取、拖拽、多模态 content 构建 |

---

### Task 1: 更新 server.py 支持多模态 Message

**Files:**
- 修改: `server.py`（第 33-35 行 Message 模型，及 generate() 中序列化）

- [ ] **Step 1: 读取当前 server.py 第 33-41 行，确认 Message 模型位置**

```bash
grep -n "class Message\|class ChatRequest\|content" /Users/hw-edit/Desktop/h00484736/api-web-project/server.py
```

预期输出含：`class Message(BaseModel):` 和 `content: str`

- [ ] **Step 2: 替换 Message 模型为多模态联合类型**

将 `server.py` 中以下代码：
```python
class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
```

替换为：
```python
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
```

- [ ] **Step 3: 验证语法**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "import ast; ast.parse(open('server.py').read()); print('syntax OK')"
```

预期输出：`syntax OK`

- [ ] **Step 4: 验证 Pydantic 能正确解析纯文本和多模态两种格式**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
ANTHROPIC_API_KEY=test python -c "
from server import Message, TextBlock, ImageBlock

# 纯文本格式（向后兼容）
m1 = Message(role='user', content='hello')
assert m1.content == 'hello', 'plain text failed'

# 多模态格式
m2 = Message(role='user', content=[
    TextBlock(type='text', text='看这张图'),
    ImageBlock(type='image', source={'type':'base64','media_type':'image/png','data':'abc'})
])
assert isinstance(m2.content, list), 'multimodal failed'
assert len(m2.content) == 2, 'block count failed'

print('validation OK')
"
```

预期输出：`validation OK`

- [ ] **Step 5: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add server.py
git commit -m "feat: support multimodal content blocks in Message model"
```

---

### Task 2: 更新 claude.html 添加文件附加功能

**Files:**
- 修改: `pages/claude.html`（CSS 新增、HTML 结构调整、JS 逻辑扩展）

- [ ] **Step 1: 在 `<style>` 块末尾（`</style>` 前）添加附件相关 CSS**

在现有 `.empty-hint { ... }` 规则后、`</style>` 前插入：

```css
    .attachments-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }
    .attachment-tag {
      display: flex;
      align-items: center;
      gap: 5px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 3px 8px;
      font-size: .8rem;
      color: var(--text);
    }
    .attachment-tag button {
      background: none;
      color: var(--text-muted);
      padding: 0 2px;
      font-size: .85rem;
      line-height: 1;
      border-radius: 3px;
      min-width: unset;
    }
    .attachment-tag button:hover { color: var(--error); background: none; }
    .drag-over {
      border-color: var(--accent) !important;
      background: rgba(108,124,255,.06) !important;
    }
```

- [ ] **Step 2: 替换 HTML 输入区域，新增附件预览行、文件 input 和 📎 按钮**

将现有：
```html
    <div class="input-row">
      <textarea id="userInput" placeholder="输入消息… (Enter 发送，Shift+Enter 换行)"></textarea>
      <button class="btn-primary" id="sendBtn">发送</button>
      <button class="btn-secondary" id="clearBtn">清空</button>
    </div>
```

替换为：
```html
    <div class="attachments-row" id="attachmentsRow"></div>
    <div class="input-row">
      <textarea id="userInput" placeholder="输入消息… (Enter 发送，Shift+Enter 换行)"></textarea>
      <input type="file" id="fileInput" multiple
             accept="image/jpeg,image/png,image/gif,image/webp,application/pdf,text/plain,text/csv,text/markdown,.md"
             style="display:none">
      <button class="btn-secondary" id="attachBtn" title="附加文件">📎</button>
      <button class="btn-primary" id="sendBtn">发送</button>
      <button class="btn-secondary" id="clearBtn">清空</button>
    </div>
```

- [ ] **Step 3: 在 `<script type="module">` 顶部，紧接 import 语句后，新增常量和 DOM 引用**

在现有：
```js
    const messagesEl   = document.getElementById('messages');
    const userInput    = document.getElementById('userInput');
    const sendBtn      = document.getElementById('sendBtn');
    const clearBtn     = document.getElementById('clearBtn');
    const modelSelect  = document.getElementById('modelSelect');
    const systemPrompt = document.getElementById('systemPrompt');

    let history = [];
```

替换为：
```js
    const messagesEl    = document.getElementById('messages');
    const userInput     = document.getElementById('userInput');
    const sendBtn       = document.getElementById('sendBtn');
    const clearBtn      = document.getElementById('clearBtn');
    const modelSelect   = document.getElementById('modelSelect');
    const systemPrompt  = document.getElementById('systemPrompt');
    const attachmentsRow = document.getElementById('attachmentsRow');
    const fileInput     = document.getElementById('fileInput');
    const attachBtn     = document.getElementById('attachBtn');

    const ACCEPTED_TYPES = {
      'image/jpeg': 'image', 'image/png': 'image',
      'image/gif': 'image',  'image/webp': 'image',
      'application/pdf': 'document',
      'text/plain': 'document', 'text/csv': 'document', 'text/markdown': 'document',
    };
    const MAX_SIZE = 20 * 1024 * 1024;

    let history = [];
    let attachments = []; // { name, mimeType, base64 }
```

- [ ] **Step 4: 在 `addError()` 函数后添加文件处理函数**

在现有 `addError()` 函数定义之后插入：

```js
    function readFileAsBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload  = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(new Error(`文件读取失败：${file.name}`));
        reader.readAsDataURL(file);
      });
    }

    async function processFiles(files) {
      for (const file of files) {
        if (file.type.startsWith('video/')) {
          addError('不支持视频文件，请上传图片或文档'); continue;
        }
        if (!ACCEPTED_TYPES[file.type]) {
          addError(`不支持的文件类型：${file.name}`); continue;
        }
        if (file.size > MAX_SIZE) {
          addError(`文件超过 20MB 限制：${file.name}`); continue;
        }
        try {
          const base64 = await readFileAsBase64(file);
          attachments.push({ name: file.name, mimeType: file.type, base64 });
          renderAttachments();
        } catch (err) { addError(err.message); }
      }
    }

    function renderAttachments() {
      attachmentsRow.innerHTML = '';
      attachments.forEach((att, i) => {
        const icon = att.mimeType.startsWith('image/') ? '🖼️' : '📄';
        const tag  = document.createElement('div');
        tag.className = 'attachment-tag';
        tag.innerHTML = `<span>${icon} ${att.name}</span><button data-i="${i}">✕</button>`;
        tag.querySelector('button').addEventListener('click', () => {
          attachments.splice(i, 1);
          renderAttachments();
        });
        attachmentsRow.appendChild(tag);
      });
    }
```

- [ ] **Step 5: 替换 `setInputEnabled()` 函数，新增对 attachBtn 和 fileInput 的控制**

将现有：
```js
    function setInputEnabled(enabled) {
      userInput.disabled = !enabled;
      sendBtn.disabled   = !enabled;
    }
```

替换为：
```js
    function setInputEnabled(enabled) {
      userInput.disabled  = !enabled;
      sendBtn.disabled    = !enabled;
      attachBtn.disabled  = !enabled;
      fileInput.disabled  = !enabled;
    }
```

- [ ] **Step 6: 替换 `send()` 函数，支持多模态 content 构建**

将现有完整 `send()` 函数替换为：

```js
    async function send() {
      const text = userInput.value.trim();
      if (!text && attachments.length === 0) return;

      userInput.value = '';

      let content;
      if (attachments.length === 0) {
        content = text;
      } else {
        content = [];
        if (text) content.push({ type: 'text', text });
        for (const att of attachments) {
          if (att.mimeType.startsWith('image/')) {
            content.push({ type: 'image', source: { type: 'base64', media_type: att.mimeType, data: att.base64 } });
          } else {
            content.push({ type: 'document', source: { type: 'base64', media_type: att.mimeType, data: att.base64 } });
          }
        }
      }

      const displayText = text + (attachments.length > 0 ? `\n[📎 ${attachments.length} 个附件]` : '');
      addBubble('user', displayText);
      history.push({ role: 'user', content });

      attachments = [];
      renderAttachments();

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
```

- [ ] **Step 7: 在 `clearBtn` 事件监听后添加附件和拖拽相关事件**

在现有：
```js
    clearBtn.addEventListener('click', () => {
      history = [];
      messagesEl.innerHTML = '<p class="empty-hint">发送消息开始对话</p>';
    });
```

之后追加：

```js
    attachBtn.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', () => {
      processFiles([...fileInput.files]);
      fileInput.value = '';
    });

    messagesEl.addEventListener('dragover', e => {
      e.preventDefault();
      messagesEl.classList.add('drag-over');
    });
    messagesEl.addEventListener('dragleave', () => messagesEl.classList.remove('drag-over'));
    messagesEl.addEventListener('drop', e => {
      e.preventDefault();
      messagesEl.classList.remove('drag-over');
      processFiles([...e.dataTransfer.files]);
    });
```

- [ ] **Step 8: 验证文件大小合理**

```bash
ls -lh /Users/hw-edit/Desktop/h00484736/api-web-project/pages/claude.html
```

预期文件大小 > 7KB（原 6.7KB + 新增内容）

- [ ] **Step 9: 提交**

```bash
cd /Users/hw-edit/Desktop/h00484736/api-web-project
git add pages/claude.html
git commit -m "feat: add multi-file attachment support with drag-and-drop"
```
