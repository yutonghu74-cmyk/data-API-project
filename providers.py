"""通用 JSON dot-path 提取器(Spec 2)。

用法:
    extract_json_value({"data": {"balance": 78.5}}, "data.balance")  → 78.5
    extract_json_value({"items": [{"x": 1}]},      "items.0.x")      → 1.0
    extract_json_value({"x": 1},                   "missing")        → None
    extract_json_value({"x": "abc"},               "x")              → None  (转 float 失败)
    extract_json_value(None,                       "any")            → None
    extract_json_value({"x": 1},                   "")               → None

后端 quota-all endpoint 用它从供应商响应里提取 total / balance / used 数值。
JSON path 由管理员在 Modal A 里配置(per-account, per-endpoint)。
"""


def extract_json_value(json_obj, path):
    if not path or json_obj is None:
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
