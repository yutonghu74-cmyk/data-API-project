# 文件上传功能 — 设计规格

**日期：** 2026-04-28  
**状态：** 已批准

---

## 目标

为 `pages/claude.html` 添加多文件上传功能，支持图片和文档，通过 base64 内联方式发送给 Claude API。

---

## 支持的文件类型

| 类型 | 格式 | Claude API 内容块类型 |
|------|------|----------------------|
| 图片 | JPG/PNG/GIF/WEBP | `image` + base64 |
| 文档 | PDF | `document` + base64 (application/pdf) |
| 文本 | TXT/CSV/MD | `document` + base64 (text/plain) |
| 视频 | 任意 | 不支持，显示错误提示 |

---

## 架构

改动范围仅限两个文件：

```
pages/claude.html   ← 文件附加 UI + base64 读取 + 拖拽支持
server.py           ← Message.content 支持多模态内容块数组
```

`stream.js`、`request.js`、`assets/css/base.css` 无需改动。

---

## 前端设计（claude.html）

### UI 布局

```
┌─────────────────────────────────────────┐
│  [附件预览区] 📄 file.pdf ✕  🖼️ img.png ✕ │
├─────────────────────────────────────────┤
│  [输入框]              [📎] [发送] [清空] │
└─────────────────────────────────────────┘
```

### 交互行为

- 点击 📎 按钮打开文件选择器（支持多选，`accept` 限定为图片+PDF+文本）
- 拖拽文件到聊天区域（`.messages` 元素）也可附加文件
- 每个附件显示为小标签：文件名 + ✕ 删除按钮
- 视频文件（`video/*`）上传时显示错误气泡，不添加到附件列表
- 发送后附件区清空
- 发送中禁用 📎 按钮和文件选择器

### 数据流

1. 用户选择/拖入文件
2. 前端用 `FileReader.readAsDataURL()` 转为 base64
3. 存入 `attachments` 数组：`{ name, mimeType, base64 }`
4. 发送时构建多模态 content 数组：
   - 文本部分：`{ type: "text", text: "..." }`
   - 图片：`{ type: "image", source: { type: "base64", media_type: "image/png", data: "..." } }`
   - 文档：`{ type: "document", source: { type: "base64", media_type: "application/pdf", data: "..." } }`
5. 纯文字消息（无附件）保持原有 `content: string` 格式

---

## 后端设计（server.py）

### 新增 Pydantic 模型

```python
class TextBlock(BaseModel):
    type: Literal["text"]
    text: str

class ImageBlock(BaseModel):
    type: Literal["image"]
    source: dict  # { type: "base64", media_type: "...", data: "..." }

class DocumentBlock(BaseModel):
    type: Literal["document"]
    source: dict  # { type: "base64", media_type: "...", data: "..." }

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[TextBlock | ImageBlock | DocumentBlock]
```

`/chat` 端点调用 SDK 时使用 `m.model_dump()` 序列化，Anthropic SDK 原生支持此多模态结构。

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 视频文件 | 显示错误气泡："不支持视频文件，请上传图片或文档" |
| 文件读取失败 | 显示错误气泡："文件读取失败：{filename}" |
| 文件过大（>20MB） | 显示错误气泡："文件超过 20MB 限制：{filename}" |
