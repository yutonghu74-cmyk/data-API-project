# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

纯静态前端 + Python 代理的第三方 API 调用平台。无构建工具，直接用浏览器打开 HTML 文件即可运行前端。后端仅在需要代理（如 Claude API）时才需要启动。

## 启动方式

**仅前端页面（无需后端）：**
直接用浏览器打开 `index.html`，或从 `pages/` 目录打开具体页面。

**Claude API 聊天页（需要后端代理）：**
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python server.py          # 监听 :8000
# 然后打开 pages/claude.html
```

## 架构

**前端**（无框架，ES Modules）：
- `index.html` — 首页，`pages` 数组驱动的导航卡片，新增页面后在此注册
- `pages/` — 每个 API 对应一个独立 HTML 文件，使用 `pages/template.html` 复制新建
- `assets/css/base.css` — 全局暗色主题，CSS 变量驱动，所有页面共享
- `assets/js/request.js` — 通用 `fetch` 封装（非流式）
- `assets/js/stream.js` — SSE 流式读取封装，供 Claude 等流式 API 使用
- `config/api.config.js` — 集中管理各 API 的 Key 和 Base URL

**后端**（Claude API 专用代理）：
- `server.py` — FastAPI，透传 Anthropic SSE 流给浏览器
- 端点：`POST /chat`、`GET /models`、`GET /health`
- CORS 允许 `localhost:*` 和 `file://`（`null` origin）

## 新增 API 页面

1. 复制 `pages/template.html` 为新文件（如 `pages/weather.html`）
2. 在 `config/api.config.js` 添加该 API 的配置
3. 在 `index.html` 的 `pages` 数组添加一条记录以在首页显示导航卡片
4. 流式 API 用 `stream.js`，普通 API 用 `request.js`

## 设计规格

已批准的功能规格存放于 `docs/superpowers/specs/`，实现前请先阅读对应文档。
