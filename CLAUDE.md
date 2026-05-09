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

- 顶栏、侧边栏等公共 UI 必须用 sidebar.js 动态注入,
     不要在每个页面 HTML 里硬编码,以保持单点修改


# 架构原则

## 关键约束
- 顶栏(topBar)由 assets/js/sidebar.js 通过 `initSidebar()` 动态注入,
  **任何 HTML 页面都不应该硬编码 <div class="top-bar">**
- 面包屑(breadcrumb)在 HTML 里直接写,但 toggle/brand/userBar 元素由 sidebar.js 动态插入

## 改动前必读
- 涉及导航/布局的改动,先 `git log --oneline assets/js/sidebar.js` 看历史
- 不要把"集中管理的逻辑"改成"分散硬编码"
- 任何破坏性架构调整,先解释为什么,再动手

## 还原策略
- 如果发现某段代码看起来"重复"或"应该集中",先查 git 历史
- 如果 git 里曾经有更优的实现被删除,优先还原而不是接受现状
