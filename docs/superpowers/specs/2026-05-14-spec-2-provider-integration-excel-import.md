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
| C3 | `server/providers.py` 通用 dot-path 提取器(无 per-provider 代码),JSON 路径由 Modal A 注册人配置 |
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
| 6 | exhausted:**3 次 0 + 12h 间隔** 才标 exhausted,缓存 `last_total/balance/used/quota_at` + `zero_count/last_zero_at` 6 列;**终态不可恢复**(用完新建 key) |
| 7 | exhausted=1 时下次打开**完全跳过**该 key 的网络调用,显示缓存值 + "(已用完,截至 YYYY-MM-DD)" |
| 8 | Parser = **通用 dot-path 提取器**(无 per-provider Python 代码);**Modal A 让注册人为每个接口额外填一个 JSON 字段名**(如 `total_granted` 或 `data.balance`) |
| 9 | Excel:**a2** 逐行成功/失败 + **b1** 提供模板下载 + **不设行数上限** + **d2** case-insensitive + trim |
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
         │      providers.extract_json_value(resp, json_path)
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

### 新增字段

**A. 在 `api_keys` 表加 6 列**(原计划 4 列 + 3-strike 新增的 `zero_count` `last_zero_at`):

```sql
ALTER TABLE api_keys ADD COLUMN last_total       REAL;
ALTER TABLE api_keys ADD COLUMN last_balance     REAL;
ALTER TABLE api_keys ADD COLUMN last_used        REAL;
ALTER TABLE api_keys ADD COLUMN last_quota_at    TEXT;
ALTER TABLE api_keys ADD COLUMN zero_count       INTEGER DEFAULT 0;  -- 连续读到 0 的次数
ALTER TABLE api_keys ADD COLUMN last_zero_at     TEXT;                -- 最近一次读到 0 的时间
```

**B. 在 `accounts` 表加 3 列**(每个接口的 JSON 提取路径):

```sql
ALTER TABLE accounts ADD COLUMN quota_total_json_path  TEXT DEFAULT '';
ALTER TABLE accounts ADD COLUMN balance_json_path      TEXT DEFAULT '';
ALTER TABLE accounts ADD COLUMN cost_json_path         TEXT DEFAULT '';
```

含义:
- 每次拉取成功后写入 `last_*`,作为"最近一次成功值"
- `exhausted=1` 时下次打开仅读 `last_*` 4 列,不调网络
- `zero_count` 累计连续 0 次数,3 即触发 exhausted
- `last_zero_at` 用于判定"距上次 0 是否超过 12h",超过才计一次新 0
- `quota_total_json_path` / `balance_json_path` / `cost_json_path` 是 dot-path,例如 `total_granted` 或 `data.balance.amount`

### 迁移机制

**不写独立脚本。** 9 列都是 SQLite 安全的 ALTER ADD COLUMN,放进 `server.init_db()` 现有 try/except 模式:

```python
# 在 init_db() 现有 api_keys CREATE TABLE 后面
for col, defn in [
    ("last_total",       "REAL"),
    ("last_balance",     "REAL"),
    ("last_used",        "REAL"),
    ("last_quota_at",    "TEXT"),
    ("zero_count",       "INTEGER DEFAULT 0"),
    ("last_zero_at",     "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {defn}")
        conn.commit()
    except Exception:
        pass

# accounts 三列
for col, defn in [
    ("quota_total_json_path", "TEXT DEFAULT ''"),
    ("balance_json_path",     "TEXT DEFAULT ''"),
    ("cost_json_path",        "TEXT DEFAULT ''"),
    
]:
    try:
        conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {defn}")
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
                   a.quota_total_path, a.balance_path, a.cost_path,
                   a.quota_total_json_path, a.balance_json_path, a.cost_json_path
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

        # === 3-strike exhaustion 逻辑 ===
        new_zero_count = r["zero_count"] or 0
        new_last_zero_at = r["last_zero_at"]
        new_exhausted = 0
        if balance is not None and balance == 0:
            # 距上次 0 是否超过 12h?如果是计一次新 0
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            should_count = True
            if new_last_zero_at:
                last = _dt.fromisoformat(new_last_zero_at)
                if (now - last).total_seconds() < 12 * 3600:
                    should_count = False  # 12h 没到,不重复计
            if should_count:
                new_zero_count += 1
                new_last_zero_at = now_iso
            if new_zero_count >= 3:
                new_exhausted = 1
        elif balance is not None and balance > 0:
            # 非零归零计数器
            new_zero_count = 0
            new_last_zero_at = None

        results[kid] = {
            "total": total, "balance": balance, "used": used,
            "exhausted": bool(new_exhausted), "from_cache": False,
            "partial": partial,
            "zero_count": new_zero_count,  # 调试用,前端可不展示
        }
        with get_db() as conn:
            conn.execute("""
                UPDATE api_keys
                SET last_total=?, last_balance=?, last_used=?, last_quota_at=?,
                    zero_count=?, last_zero_at=?, exhausted=?
                WHERE id=?
            """, (total, balance, used, now_iso,
                  new_zero_count, new_last_zero_at, new_exhausted,
                  r["id"]))
            conn.commit()

    return {"fetched_at": now_iso, "results": results}


async def _fetch_one_key(r: dict) -> tuple:
    """3 接口并发拉取。返回 (total, balance, used),失败的为 None。"""
    base = (r["provider_backend_url"] or r["base_url"] or "").rstrip("/")
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
    from providers import extract_json_value
    total   = extract_json_value(raw[0], r["quota_total_json_path"]) if not isinstance(raw[0], Exception) else None
    balance = extract_json_value(raw[1], r["balance_json_path"])     if not isinstance(raw[1], Exception) else None
    used    = extract_json_value(raw[2], r["cost_json_path"])        if not isinstance(raw[2], Exception) else None
    return total, balance, used
```

**3-strike 逻辑直觉示例:**
- T0 读到 0 → zero_count=1, last_zero_at=T0
- T0+1h 又读到 0 → 12h 没到,不计 → zero_count 仍=1
- T0+13h 读到 0 → 12h 过了,zero_count=2
- T0+25h 读到 0 → zero_count=3 → 标 exhausted=1
- 中间任意一次读到 >0 → zero_count 归 0

**注:** 这个逻辑依赖"用户每天都打开 keys.html"才能累计 strike;如果两次 quota-all 调用相隔 > 12h,自然累计。如果用户一天点 100 次刷新,每次都 0,只算 1 次(因为 12h 间隔限制)。

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
- 表头识别失败 → `{"detail": "未找到 API名称 / API key 列"}`
- 文件非 xlsx → `{"detail": "仅支持 .xlsx 格式"}`
- (无行数上限,大文件由 server 内存承担风险 — 见 §风险)

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

## providers.py — 通用 JSON 提取器

**没有 per-provider Python 代码。** 所有 provider 都通过 Modal A 配置 dot-path 提取(如 `total_granted` 或 `data.balance.amount`)。

新建文件 `providers.py`(项目根):

```python
"""通用 JSON dot-path 提取器。

签名:
    extract_json_value(json_obj, path: str) -> float | None

path 例:
  "total_granted"       → json["total_granted"]
  "data.balance"        → json["data"]["balance"]
  "credit.0.amount"     → json["credit"][0]["amount"]   (支持数组下标)
  ""                    → None

提取后强制转 float;转不了 / 路径不存在 / 中间 None → 返回 None。
"""

def extract_json_value(json_obj, path):
    if not path:
        return None
    if json_obj is None:
        return None
    cur = json_obj
    for p in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    if cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None
```

**新增 provider 流程(完全前端):**
1. 管理员在 Modal A 选/填 provider
2. 填 base_url、后端网址、3 个接口 URL path
3. 填 3 个对应的 JSON 提取 path(如 `total_granted` / `total_available` / `total_used`)
4. 保存

无需写代码、无需 server 重启,所有信息都在 DB 里。

**`provider` 字段的角色变化:** 从"决定用哪个 parser 的 key"降级为**纯标签**(用于分组、筛选、自动填模板)。`PARSERS` dict 不存在了。

---

## UI 改造(`pages/keys.html`)

### Modal A — 标签微调 + 加 JSON 路径输入

| 旧字段 label | 新 label |
|---|---|
| `费用接口` | `已用接口` |

字段名(`cost_path`)不动。

**新增 3 个 JSON 路径输入框**(每个接口 URL 旁边一个):

```
[总额接口 URL]    [/v1/dashboard/billing/credit_grants]
[总额 JSON 字段]   [total_granted]                                ← 新增

[余额接口 URL]    [/v1/dashboard/billing/credit_grants]
[余额 JSON 字段]   [total_available]                              ← 新增

[已用接口 URL]    [/v1/dashboard/billing/credit_grants]
[已用 JSON 字段]   [total_used]                                   ← 新增
```

**说明文字:**(在 Modal 顶部加一行小字)
> JSON 字段支持点号嵌套(如 `data.balance`)和数组下标(如 `items.0.amount`)

提交时 body 加 3 个新字段:
```javascript
const body = {
  // ... 现有
  quota_total_path:      ...,
  quota_total_json_path: document.getElementById('aQuotaJsonPath').value.trim(),
  balance_path:          ...,
  balance_json_path:     document.getElementById('aBalanceJsonPath').value.trim(),
  cost_path:             ...,
  cost_json_path:        document.getElementById('aCostJsonPath').value.trim(),
};
```

后端 `AccountIn` Pydantic 加 3 个对应字段(默认空字符串):

```python
class AccountIn(BaseModel):
    # ... 现有
    quota_total_json_path: str = ""
    balance_json_path:     str = ""
    cost_json_path:        str = ""
```

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
  const fields = ['aBaseUrl','aBackend','aQuotaPath','aQuotaJsonPath',
                  'aBalancePath','aBalanceJsonPath','aCostPath','aCostJsonPath'];
  const anyFilled = fields.some(id => document.getElementById(id).value.trim());
  if (anyFilled && !confirm(
    `检测到 provider "${v}" 已有 ${matches.length} 个帐号,应用其模板会覆盖你已填的字段?`
  )) return;
  document.getElementById('aBaseUrl').value          = tpl.base_url || '';
  document.getElementById('aBackend').value          = tpl.provider_backend_url || '';
  document.getElementById('aQuotaPath').value        = tpl.quota_total_path || '';
  document.getElementById('aQuotaJsonPath').value    = tpl.quota_total_json_path || '';
  document.getElementById('aBalancePath').value      = tpl.balance_path || '';
  document.getElementById('aBalanceJsonPath').value  = tpl.balance_json_path || '';
  document.getElementById('aCostPath').value         = tpl.cost_path || '';
  document.getElementById('aCostJsonPath').value     = tpl.cost_json_path || '';
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
# 通用 dot-path 提取器行为
def test_extract_simple_key():           # {"a":1}, "a" → 1.0
def test_extract_nested():               # {"a":{"b":2}}, "a.b" → 2.0
def test_extract_array_index():          # {"a":[10,20]}, "a.1" → 20.0
def test_extract_missing_path():         # {"a":1}, "x" → None
def test_extract_through_none():         # {"a":None}, "a.b" → None
def test_extract_empty_path():           # any, "" → None
def test_extract_non_numeric():          # {"a":"hi"}, "a" → None
def test_extract_int_to_float():         # {"a":5}, "a" → 5.0
def test_extract_list_invalid_index():   # {"a":[1]}, "a.5" → None
```

### 2. 端点测试 — 扩展 `tests/test_admin.py`

```python
class TestQuotaAll:
    - 无 token → 401
    - 用户视野空 (无 accounts) → results={} 不报错
    - exhausted=1 的 key 直接读缓存,不发外部请求(monkeypatch 验证 httpx 没被调用)
    - 非 exhausted 的 key 调 _fetch_one_key(monkeypatch httpx 返回固定 JSON)
    - balance=0 第一次:zero_count=1,不 exhausted
    - 第二次 0(同次调用模拟 12h+ 后):zero_count=2
    - 第三次 0:exhausted=1 + 写 last_*
    - 12h 内重复 0:zero_count 不再加
    - 中间一次非 0:zero_count 归 0
    - 部分接口失败仍返回 partial=true + 部分字段有值
    - 全部接口失败 → results[id] = {"error": "..."}
    - JSON path 取不到字段 → 该字段 None,partial=true

class TestImportExcel:
    - 上传 .xlsx 5 行,2 重复,1 空名 → imported=2, skipped=2, errors=1
    - 上传 .csv → 400
    - 上传 1000 行(无上限)→ 处理完(虽然慢)
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
  □ 上传 1000 行(无上限) → 处理完(可能慢)
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
| `providers.extract_json_value` | Spec 2 通用提取器 | Spec 3 不动 |
| `accounts.{quota_total,balance,cost}_json_path` | Spec 2 收集 | Spec 3 不动 |
| `api_keys.{zero_count, last_zero_at}` | Spec 2 用于 3-strike | Spec 3 不动 |

---

## 风险 / 已知不足

| 风险 | 缓解 |
|---|---|
| 100 个 key × 3 接口 = 300 并发 GET,可能超过 fd / asyncio 默认上限 | 当前规模(3 个 key)无问题;到达上限再加 `asyncio.Semaphore(50)` 限流 |
| 供应商接口 SCHEMA 变更,JSON path 取不到值 | 该字段返回 None,前端表格 hover 显示"字段缺失";管理员去 Modal A 改 JSON path |
| `last_balance` 缓存可能过时(供应商外部消费)| Spec 2 接受这个 trade-off:正常 key 每次刷新,exhausted 是终态不再消费 |
| Excel 不设行数上限,大文件可能 OOM / 长阻塞 | 用户明确选择;100k 行级 Excel 如果遇到问题再加 `Semaphore` 或 streaming 解析 |
| 导入 Excel 复用 `create_api_key` 是同步;1000 行预计 1-2 秒,10k 行可能 10s+ 阻塞 worker | 大文件场景再考虑后台任务或分批 |
| Modal A 的"覆盖"提示 = `confirm()` 原生对话框 UX 一般 | 现有 keys.html 也用 `confirm()`,保持一致;Spec 3 可统一升级 |
| 3-strike exhausted 依赖"用户每天打开页面累计 strike";若 7 天无人访问,虽 key 已用完 1 周也不会标 exhausted | 接受 — 没人访问时 exhausted 标不标也不影响业务 |
| JSON path 不支持复杂 jq 语法(过滤、聚合)| Spec 2 只做 dot + 数组下标;未来需要再升级到 jq |

---

## 验收标准

1. `pytest tests/` 全绿(包括 `test_providers.py` 和扩展的 `test_admin.py`)
2. `bash verify.sh` 通过(加 `/admin/accounts/quota-all` smoke check)
3. 浏览器手测 checklist 全勾
4. server.py grep 无新的 `time.sleep` / 阻塞调用(quota-all 必须真正异步)
5. Spec 1 测试套件无回归
