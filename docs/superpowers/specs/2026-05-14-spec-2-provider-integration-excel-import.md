# Spec 2 — 供应商接口集成 + Excel 导入

**日期:** 2026-05-14
**状态:** 设计已确认,等待实施计划
**前置:** Spec 1(`2026-05-13-account-management-spec-1-design.md`)已落地

---

## 背景

Spec 1 完成了三层 schema(accounts → sub_accounts → api_keys)和 CRUD,但 keys.html 表格的"总额/余额"列仍是灰色 `--`,Modal A 收集到的供应商接口字段(`provider_backend_url`, `quota_total_path`, `balance_path`, `cost_path`)未被使用。Spec 2 把这些钩子接通,并加 Excel 批量导入 keys。

---

## 目标

| ID | 目标 |
|---|---|
| C1 | Modal A 选供应商时**前端从已有 accounts 派生模板**自动填字段(无需新表) |
| C2 | 接通 Modal A 已收集的 provider 接口字段(无 schema 改动) |
| C3 | `server/providers.py` 单文件 dict registry,per-provider 解析器 |
| C4 | `keys.html` 总额/余额列实时调供应商接口,失败显示灰 "--" + hover 错误原因 |
| C5 | exhausted(余额=0)的 key 缓存最后值,下次跳过 fetch |
| D1 | `POST /admin/sub-accounts/{id}/api-keys/import-excel` 后端 openpyxl 解析 |
| D2 | `GET /admin/api-keys/template.xlsx` 模板下载 |

**非范围(Spec 3 的事):**
- 申请/审核 cascade 表单
- 自动分配 key
- claude.html / batch.html 改造
- 彻底废除 `x-admin-password`
- "费用接口"返回值的会计/对账逻辑(本 spec 只显示数字)

---

## 关键决定(brainstorm 总结)

| # | 决定 |
|---|---|
| 1 | 三接口语义:`总额 = 余额 + 已用`(不变量),都返回数字 |
| 2 | 字段名 `cost_path` 保留(SQLite 改名成本高),UI/parser 内部当"已用"用 |
| 3 | provider_templates **不建独立表** — 从 `allAccounts.filter(provider=X)` 取最近的 account 字段做"模板",纯前端逻辑 |
| 4 | 自动填行为 = **iii 提示后用户决定**("检测到 provider 'X' 的 N 个已有帐号,应用其模板?Yes/No") |
| 5 | 性能:**单聚合端点 `/admin/accounts/quota-all`**,后端 `asyncio.gather` 并发,10s timeout |
| 6 | exhausted:`balance==0` → `exhausted=1`,缓存 `last_total/balance/used/quota_at` 4 列;**终态不可恢复**(用完新建 key) |
| 7 | exhausted=1 时下次打开**完全跳过**该 key 的网络调用,显示缓存值 + "(已用完,截至 YYYY-MM-DD)" |
| 8 | parser registry = **`server/providers.py` 单文件 + dict** `PARSERS = {"yibu": parse_yibu, ...}` |
| 9 | Excel:**a2** 逐行成功/失败 + **b1** 提供模板下载 + **c1** 100 行上限 + **d2** case-insensitive + trim |
| 10 | 失败显示:**gray "--" + hover 错误原因** |
| 11 | 鉴权 / 删除保护规则全部沿用 Spec 1 |

---

## 架构

```
[keys.html admin section]
         │ X-Token
         │
         ├─ GET /admin/accounts/quota-all  (1 次/页面打开)
         │      │
         │      ▼
         │  server.py async handler
         │      │
         │      ├─ list visible api_keys (visibility_filter)
         │      ├─ for exhausted=1:    用 last_* 缓存,不调网络
         │      └─ for non-exhausted:  asyncio.gather 并发
         │              │
         │              ▼
         │      providers.py PARSERS[provider](resp, kind)
         │              │
         │              ▼
         │      httpx.AsyncClient(timeout=10) Bearer Key
         │              │
         │              ▼
         │      供应商 /v1/billing/...
         │
         └─ POST /admin/sub-accounts/{id}/api-keys/import-excel
            multipart .xlsx → openpyxl → 复用 Spec 1 create_api_key
```

---

## 数据库 schema

### 4 个新列(在 `api_keys` 表)

```sql
ALTER TABLE api_keys ADD COLUMN last_total       REAL;
ALTER TABLE api_keys ADD COLUMN last_balance     REAL;
ALTER TABLE api_keys ADD COLUMN last_used        REAL;
ALTER TABLE api_keys ADD COLUMN last_quota_at    TEXT;
```

含义:
- 每次拉取成功后写入,作为"最近一次成功值"
- `exhausted=1` 时下次打开仅读这 4 列,不调网络
- `last_quota_at` 用 ISO8601 字符串

### 迁移机制

**不写独立脚本。** 4 列都是 SQLite 安全的 ALTER ADD COLUMN,放进 `server.init_db()` 的现有 try/except 模式:

```python
# 在 init_db() 现有 api_keys CREATE TABLE 后面
for col, defn in [
    ("last_total",     "REAL"),
    ("last_balance",   "REAL"),
    ("last_used",      "REAL"),
    ("last_quota_at",  "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {defn}")
        conn.commit()
    except Exception:
        pass
```

**桥接 VIEW `api_configs` 不动**:Spec 1 建的 VIEW 不引用这 4 列,继续工作。

---

## API 端点

### 1. `GET /admin/accounts/quota-all` — 主力

**鉴权:** `x-token` + Spec 1 visibility_filter
**实现签名:** `async def`(必须,要 `await asyncio.gather`)

**响应:**
```json
{
  "fetched_at": "2026-05-14T...",
  "results": {
    "1": {"total": 100, "balance": 78.5, "used": 21.5, "exhausted": false, "from_cache": false},
    "2": {"total": 100, "balance": 0,    "used": 100,  "exhausted": true,  "from_cache": true,  "cached_at": "2026-05-10T..."},
    "3": {"error": "502: Bad Gateway"},
    "4": {"total": null, "balance": 78.5, "used": null, "exhausted": false, "from_cache": false, "partial": true}
  }
}
```

注:`partial=true` 表示 3 个接口里部分失败,有的字段是 `null`。

**伪代码:**
```python
@app.get("/admin/accounts/quota-all")
async def quota_all(x_token: str = Header(default="")):
    user = get_current_user(x_token)
    where_sql, where_params = visibility_filter(user)
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT k.*, a.provider, a.base_url, a.provider_backend_url,
                   a.quota_total_path, a.balance_path, a.cost_path
            FROM api_keys k
            JOIN sub_accounts s ON s.id = k.sub_account_id
            JOIN accounts a     ON a.id = s.account_id
            WHERE {where_sql}
        """, where_params).fetchall()

    results = {}
    pending = []
    for r in rows:
        if r["exhausted"]:
            results[str(r["id"])] = {
                "total": r["last_total"],
                "balance": 0,
                "used": r["last_used"],
                "exhausted": True,
                "from_cache": True,
                "cached_at": r["last_quota_at"],
            }
        else:
            pending.append((r, asyncio.create_task(_fetch_one_key(dict(r)))))

    fetched = await asyncio.gather(*[t for _, t in pending], return_exceptions=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    for (r, _), result in zip(pending, fetched):
        kid = str(r["id"])
        if isinstance(result, Exception):
            results[kid] = {"error": str(result)}
            continue
        total, balance, used = result
        partial = (total is None) or (balance is None) or (used is None)
        results[kid] = {
            "total": total, "balance": balance, "used": used,
            "exhausted": False, "from_cache": False,
            "partial": partial,
        }
        # 写缓存,余额=0 时翻 exhausted
        with get_db() as conn:
            conn.execute("""
                UPDATE api_keys
                SET last_total=?, last_balance=?, last_used=?, last_quota_at=?,
                    exhausted=?
                WHERE id=?
            """, (total, balance, used, now_iso,
                  1 if (balance is not None and balance == 0) else 0,
                  r["id"]))
            conn.commit()

    return {"fetched_at": now_iso, "results": results}


async def _fetch_one_key(r: dict) -> tuple:
    """3 接口并发拉取。返回 (total, balance, used),失败的为 None。"""
    base = (r["provider_backend_url"] or r["base_url"] or "").rstrip("/")
    parser = providers.PARSERS.get(r["provider"])
    if not parser:
        raise RuntimeError(f"未知 provider: {r['provider']}")
    headers = {"Authorization": f"Bearer {r['api_key']}"}

    async def _get(path):
        if not path:
            return None  # 空 path 跳过
        async with httpx.AsyncClient(timeout=10) as cli:
            resp = await cli.get(base + path, headers=headers)
            resp.raise_for_status()
            return resp.json()

    raw = await asyncio.gather(
        _get(r["quota_total_path"]),
        _get(r["balance_path"]),
        _get(r["cost_path"]),
        return_exceptions=True,
    )
    total   = parser(raw[0], "total")    if not isinstance(raw[0], Exception) and raw[0] is not None else None
    balance = parser(raw[1], "balance")  if not isinstance(raw[1], Exception) and raw[1] is not None else None
    used    = parser(raw[2], "used")     if not isinstance(raw[2], Exception) and raw[2] is not None else None
    return total, balance, used
```

### 2. `POST /admin/sub-accounts/{sub_id}/api-keys/import-excel`

**鉴权:** x-token + 子帐号写权限(`require_owner_or_admin` of 父 account)
**Body:** `multipart/form-data`, 字段 `file`(.xlsx)

**响应 200:**
```json
{
  "imported": 5,
  "skipped_duplicates": [{"row": 3, "name": "k1"}],
  "errors": [{"row": 7, "reason": "API名称或 key 为空"}],
  "total_rows": 7
}
```

**响应 400:**
- 行数 > 100 → `{"detail": "行数超过 100 上限"}`
- 表头识别失败 → `{"detail": "未找到 API名称 / API key 列"}`
- 文件非 xlsx → `{"detail": "仅支持 .xlsx 格式"}`

**实现:**
```python
@app.post("/admin/sub-accounts/{sub_id}/api-keys/import-excel")
def import_keys_xlsx(sub_id: int, file: UploadFile = File(...),
                     x_token: str = Header(default="")):
    user = get_current_user(x_token)
    with get_db() as conn:
        sub = _get_sub_account_or_404(conn, sub_id)
        if user["role"] != "admin" and sub["account_created_by"] != user["id"]:
            raise HTTPException(403, "无权操作")

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "仅支持 .xlsx 格式")

    from openpyxl import load_workbook
    wb = load_workbook(file.file, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) > 101:  # 100 数据 + 1 表头
        raise HTTPException(400, "行数超过 100 上限")
    if not rows:
        raise HTTPException(400, "Excel 为空")

    # 识别表头(case-insensitive + trim)
    header = [str(c or "").strip().lower() for c in rows[0]]
    name_col = next((i for i, h in enumerate(header) if "名称" in h or "name" in h), -1)
    key_col  = next((i for i, h in enumerate(header) if "key"  in h), -1)
    if name_col < 0 or key_col < 0:
        raise HTTPException(400, "未找到 API名称 / API key 列")

    imported, skipped, errors = 0, [], []
    for idx, row in enumerate(rows[1:], start=2):
        name = str(row[name_col] or "").strip() if name_col < len(row) else ""
        key  = str(row[key_col]  or "").strip() if key_col  < len(row) else ""
        if not name or not key:
            errors.append({"row": idx, "reason": "API名称或 key 为空"})
            continue
        try:
            create_api_key(sub_id, ApiKeyIn(name=name, api_key=key), x_token)
            imported += 1
        except HTTPException as e:
            if e.status_code == 409:
                skipped.append({"row": idx, "name": name})
            else:
                errors.append({"row": idx, "reason": str(e.detail)})

    return {"imported": imported, "skipped_duplicates": skipped,
            "errors": errors, "total_rows": len(rows) - 1}
```

### 3. `GET /admin/api-keys/template.xlsx`

**鉴权:** 任意登录用户
**响应:** xlsx 二进制,2 列表头 + 1 行示例

```python
@app.get("/admin/api-keys/template.xlsx")
def download_template(x_token: str = Header(default="")):
    get_current_user(x_token)
    from openpyxl import Workbook
    from io import BytesIO
    wb = Workbook(); ws = wb.active; ws.title = "API keys"
    ws.append(["API名称", "API key"])
    ws.append(["示例-主key", "sk-xxxxxxxxxxxxxxxx"])
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="api_keys_template.xlsx"'},
    )
```

---

## providers.py — Parser Registry

新建文件 `server/providers.py`(或直接 `providers.py` 在项目根);通过 `PARSERS` dict 注册。

```python
"""供应商响应解析器 registry。

每个 parser 签名:
    parse(resp_json: dict | list, kind: str) -> float | None
其中 kind 是 "total" | "balance" | "used"。

如果某个 kind 在该供应商响应里不存在,返回 None。
"""

def parse_yibu(resp_json, kind: str):
    """Yibu API(OpenAI 兼容,信用额度接口)。
    示例响应(/v1/dashboard/billing/credit_grants):
      {"total_granted": 100, "total_used": 20.5, "total_available": 79.5}
    """
    if not isinstance(resp_json, dict):
        return None
    if kind == "total":
        return resp_json.get("total_granted")
    if kind == "balance":
        return resp_json.get("total_available")
    if kind == "used":
        return resp_json.get("total_used")
    return None


def parse_anthropic(resp_json, kind: str):
    """占位 — 实际 Anthropic 没有公开余额接口,Spec 2 暂不支持。"""
    return None


# 注册表
PARSERS = {
    "yibu":      parse_yibu,
    "一步":      parse_yibu,    # 现有数据有别名
    "anthropic": parse_anthropic,
}
```

**新增 provider 流程:**
1. 在 `providers.py` 加一个 `parse_xxx` 函数
2. 在 `PARSERS` dict 加一条
3. server 重启
4. 管理员在 Modal A 选这个 provider,填好 base_url + 3 个 path,保存

**未注册 provider 的处理:**
`PARSERS.get(provider)` 返回 None → `_fetch_one_key` 抛 `RuntimeError("未知 provider")` → quota-all 端点的 `results[id] = {"error": "未知 provider"}`,前端表格那行显示 hover error。

---

## UI 改造(`pages/keys.html`)

### Modal A — 标签微调

| 旧字段 label | 新 label |
|---|---|
| `费用接口` | `已用接口` |

字段名(`cost_path`)不动。

### Modal A — 供应商自动填(C1, C4 = iii 提示)

```javascript
// providerSelect 的 change 事件
const aProvider = document.getElementById('aProvider');
aProvider.addEventListener('change', () => {
  const v = aProvider.value.trim();
  if (!v) return;
  const matches = allAccounts.filter(a => a.provider === v);
  if (!matches.length) return;
  const tpl = matches[0];  // 取最近的(列表已 ORDER BY id DESC)
  // 检查当前用户是否已填了字段
  const fields = ['aBaseUrl','aBackend','aQuotaPath','aBalancePath','aCostPath'];
  const anyFilled = fields.some(id => document.getElementById(id).value.trim());
  if (anyFilled && !confirm(
    `检测到 provider "${v}" 已有 ${matches.length} 个帐号,应用其模板会覆盖你已填的字段?`
  )) return;
  document.getElementById('aBaseUrl').value     = tpl.base_url || '';
  document.getElementById('aBackend').value     = tpl.provider_backend_url || '';
  document.getElementById('aQuotaPath').value   = tpl.quota_total_path || '';
  document.getElementById('aBalancePath').value = tpl.balance_path || '';
  document.getElementById('aCostPath').value    = tpl.cost_path || '';
});
```

注:`change` 事件对 `<input list="providerList">` 在用户选 datalist 项时触发。

### 列"总额/余额"接通(C4)

`loadAll()` 完成后再额外调一次 `quota-all`,把结果填进表格。

```javascript
async function loadAll() {
  await loadAccountsTree();   // 现有逻辑(Spec 1)
  buildFlatRows();
  await refreshFilterDropdowns();
  renderTable();              // 先渲染,余额列灰 -- 占位
  refreshQuotaInline();       // 异步,完成后更新
}

async function refreshQuotaInline() {
  let data;
  try {
    data = await apiFetch('/admin/accounts/quota-all');
  } catch (e) {
    return;  // 静默失败,保留灰色 --
  }
  for (const [keyIdStr, q] of Object.entries(data.results)) {
    const tr = document.querySelector(`tr[data-key-id="${keyIdStr}"]`);
    if (!tr) continue;
    const totalCell   = tr.querySelector('[data-col="total"]');
    const balanceCell = tr.querySelector('[data-col="balance"]');
    if (q.error) {
      totalCell.title   = q.error; balanceCell.title = q.error;
      totalCell.textContent = '--'; balanceCell.textContent = '--';
      totalCell.style.color = balanceCell.style.color = 'var(--text-muted)';
      continue;
    }
    const totalDisp = q.total != null ? q.total : '--';
    const balDisp   = q.balance != null ? q.balance : '--';
    if (q.exhausted) {
      const stamp = (q.cached_at || '').slice(0,10);
      totalCell.textContent   = totalDisp;
      balanceCell.textContent = '已用完';
      balanceCell.title       = `截至 ${stamp}`;
      balanceCell.style.color = '#dc2626';
    } else {
      totalCell.textContent   = totalDisp;
      balanceCell.textContent = balDisp;
    }
  }
}
```

`renderTable()` 渲染时给行加 `data-key-id` 属性 + 给"总额/余额"两个 td 加 `data-col` 属性,方便 `refreshQuotaInline` 选择更新。

### Modal C — 加 Excel 导入按钮(D1)

Modal C 现有"新增 API key"表单底部加一个"或导入 Excel" 区:

```html
<div style="border-top:1px solid var(--border);margin-top:16px;padding-top:12px">
  <div style="display:flex;gap:8px;align-items:center">
    <span style="font-size:.85rem;color:var(--text-muted)">或批量导入:</span>
    <input type="file" id="kImportFile" accept=".xlsx" style="font-size:.85rem">
    <button type="button" class="btn btn-secondary" id="kImportBtn">导入</button>
    <a href="http://localhost:8000/admin/api-keys/template.xlsx?_t=now"
       id="kTplLink" style="font-size:.8rem;color:var(--green)">下载模板</a>
  </div>
  <p id="kImportResult" style="font-size:.8rem;margin-top:6px;min-height:14px"></p>
</div>
```

JS:
```javascript
document.getElementById('kImportBtn').addEventListener('click', async () => {
  const fileInput = document.getElementById('kImportFile');
  const subId = parseInt(document.getElementById('kSub').value);
  const resultEl = document.getElementById('kImportResult');
  if (!fileInput.files.length) { resultEl.textContent = '请先选择 .xlsx 文件'; return; }
  if (!subId) { resultEl.textContent = '请先选择子帐号'; return; }
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  resultEl.textContent = '上传中...';
  try {
    const res = await fetch(
      `http://localhost:8000/admin/sub-accounts/${subId}/api-keys/import-excel`,
      { method: 'POST', body: fd, headers: { 'X-Token': localStorage.getItem('token') || '' } }
    );
    const data = await res.json();
    if (!res.ok) { resultEl.textContent = '失败:' + (data.detail || res.status); return; }
    resultEl.textContent =
      `导入 ${data.imported} 条,跳过重复 ${data.skipped_duplicates.length} 条,失败 ${data.errors.length} 条`;
    // 关闭 modal + 刷新
    keyModalBg.classList.remove('open');
    await loadAll();
  } catch (e) {
    resultEl.textContent = '上传失败:' + e.message;
  }
});

// 模板下载链接需要带 token,改成 JS fetch + blob
document.getElementById('kTplLink').addEventListener('click', async (e) => {
  e.preventDefault();
  const res = await fetch('http://localhost:8000/admin/api-keys/template.xlsx',
                          { headers: { 'X-Token': localStorage.getItem('token') || '' } });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'api_keys_template.xlsx';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
```

---

## 测试

### 1. 单元测试 — `tests/test_providers.py`(新)

```python
# parser 行为
def test_parse_yibu_total(): ...
def test_parse_yibu_balance(): ...
def test_parse_yibu_used(): ...
def test_parse_yibu_unknown_kind_returns_none(): ...
def test_parser_for_unknown_provider(): ...
```

### 2. 端点测试 — 扩展 `tests/test_admin.py`

```python
class TestQuotaAll:
    - 无 token → 401
    - 用户视野空 (无 accounts) → results={} 不报错
    - exhausted=1 的 key 直接读缓存,不发外部请求(用 monkeypatch 验证 httpx 没被调用)
    - 非 exhausted 的 key 调 _fetch_one_key(monkeypatch parser 返回固定值)
    - balance=0 触发 exhausted=1 + 写 last_*
    - 部分接口失败仍返回 partial=true + 部分字段有值
    - 全部接口失败 → results[id] = {"error": "..."}

class TestImportExcel:
    - 上传 .xlsx 5 行,2 重复,1 空名 → imported=2, skipped=2, errors=1
    - 上传 .csv → 400
    - 上传 200 行 → 400 行数超限
    - 上传无表头匹配 → 400
    - 表头大写 / 含空格 → d2 仍能识别

class TestTemplate:
    - 下载返回 xlsx mime + 含 "API名称" / "API key" 表头
```

### 3. 手测 checklist

放进 PR 描述:
```
□ keys.html admin 登录后,打开页面观察"总额/余额"列从灰色 -- 变成数字
  □ 失败行显示灰 -- + hover 显示错误原因
  □ 余额=0 触发 exhausted,刷新页面后该行不再调外部接口(看 server log)
  □ 已 exhausted 行显示缓存值 + balance 列红色"已用完",hover 显示截至日期
□ Modal A 选已有 provider("一步")
  □ 字段空时:自动填,无提示
  □ 已有内容时:弹"应用模板会覆盖?"确认对话
  □ 选不存在的 provider:不触发任何动作
□ Modal C 导入 Excel:
  □ 下载模板 → 拿到 .xlsx 文件,用 Excel/numbers 打开看到表头
  □ 模板加几行数据 + 1 行重复 → 导入 → 看到 "导入 N 条,跳过 M 条" 提示
  □ 上传 .csv → 400 错误显示
  □ 上传超 100 行 → 400 错误
□ /chat /configs/{id}/models claude.html 仍能聊天
□ pytest 全绿
```

---

## 留给 Spec 3 的钩子

| 钩子 | 状态 | Spec 3 用 |
|---|---|---|
| `api_keys.last_balance` | Spec 2 已写 | Spec 3 自动分配"严格等于"算法直接读它做匹配,**无需重新外发请求** |
| `api_keys.exhausted` | Spec 2 用 | Spec 3 跳过 exhausted=1 的候选 |
| `quota-all` 聚合端点 | Spec 2 已建 | Spec 3 申请审核时调一次得全局快照 |
| `providers.PARSERS` | Spec 2 dict | Spec 3 不动 |

---

## 风险 / 已知不足

| 风险 | 缓解 |
|---|---|
| 100 个 key × 3 接口 = 300 并发 GET,可能超过 fd / asyncio 默认上限 | 当前规模(3 个 key)无问题;到达上限再加 `asyncio.Semaphore(50)` 限流 |
| 供应商接口 SCHEMA 变更,parser 静默返回 None | 表格 hover 会显示"返回字段缺失"或解析异常;管理员注意到后改 parser |
| `last_balance` 缓存可能过时(供应商外部消费)| Spec 2 接受这个 trade-off:正常 key 每次刷新,exhausted 是终态不再消费 |
| 导入 Excel 复用 `create_api_key` 是同步 + 用 `x_token` header | 性能 OK(100 行内);保留同步逻辑避免新写并发版本 |
| Modal A 的"覆盖"提示 = `confirm()` 原生对话框 UX 一般 | 现有 keys.html 也用 `confirm()`,保持一致;Spec 3 可统一升级 |
| `parse_anthropic` 占位返回 None,无效 | Anthropic 没有公开余额接口;实际部署若需要,可加自定义 endpoint 或代理层。Spec 2 不强制 |

---

## 验收标准

1. `pytest tests/` 全绿(包括 `test_providers.py` 和扩展的 `test_admin.py`)
2. `bash verify.sh` 通过(加 `/admin/accounts/quota-all` smoke check)
3. 浏览器手测 checklist 全勾
4. server.py grep 无新的 `time.sleep` / 阻塞调用(quota-all 必须真正异步)
5. Spec 1 测试套件无回归
