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


# Token 减负偏好(覆盖默认 superpowers 行为)

这一节明确覆盖 superpowers 技能的部分默认行为,目的是减少 token 消耗。
如果与 brainstorming / writing-plans / executing-plans 技能冲突,**以本节为准**。

## 何时**不要**用 superpowers
- 单文件 bug 修复 / 字段重命名 / 改 label 文字 / 加几行 CSS:直接动手,不开 brainstorm
- 加单一 endpoint 或修复已知问题:直接 systematic-debugging 即可,不写 spec/plan
- 改动小于 ~50 行 + 不涉及 schema 变更:直接做

## 何时**可以**用 superpowers
- 全新模块设计(数据模型、跨页面架构变更)
- 多人会读的对外文档(spec)
- 需要分阶段交付的大改造(像 Spec 1 三层重构)

## 用 brainstorming 时的减负规则
- **问题一次问完,不要一题一回合** —— 把 4-6 个澄清问题打包成一个 message,用 multi-question
- **跳过"分节呈现 + 逐节确认"** —— 直接把完整设计列出来,等用户一次性反馈
- **跳过"propose 2-3 approaches"** —— 如果方向已清晰,直接给推荐方案 + 让用户确认/否决
- **Visual Companion 默认不开** —— 除非用户明确说要看 mockup

## 用 writing-plans 时的减负规则
- **plan 文档要 lean** —— 只列任务名 + 关键设计点 + schema 变更,**不要在每个 step 抄完整代码**
- **可以写 "类似 Task N 的 PUT/DELETE pattern,改 X/Y/Z"** —— 不必照打 200 行
- **TaskList 形式即可,不需要 step 1-5 ceremony**

## 用 executing-plans 时的减负规则
- **CRUD 端点跳过 TDD** —— 标准 GET/POST/PUT/DELETE 不需要先写测试,只对**有非平凡逻辑**的端点(如去重、聚合、并发)写测试
- **测试只写 critical path** —— 不必每个错误分支都覆盖
- **commit 频次** —— 一个相关任务集合一次 commit 即可,不需要每个 task 都 commit
- **不要每次都跑全测试套件** —— 改了 X 模块就跑 X 相关 tests,最后再跑一次全套

## 默认推荐路径(从最轻到最重)
1. 直接做(< 50 行 / 无 schema)
2. 调 systematic-debugging(已知 bug)
3. brainstorm(精简模式)→ 直接执行(跳过 plan 文档)
4. brainstorm → plan(精简) → 执行(跳过 TDD)
5. 完整 superpowers 流程(仅大型新模块)
