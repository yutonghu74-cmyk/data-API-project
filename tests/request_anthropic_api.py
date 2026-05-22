"""
用 requests 直接调用 Anthropic Messages API（绕过官方 SDK）。

适合排查 SDK + httpx 行为问题（302 跳转、代理、SSL 等），也方便看清原始 HTTP 协议。

用法:
    python request_anthropic_api.py "你好"
    python request_anthropic_api.py            # 进入交互模式

环境变量（优先级高于代码默认）:
    ANTHROPIC_API_KEY        必填
    ANTHROPIC_BASE_URL       默认 https://api.anthropic.com
    ANTHROPIC_MODEL          默认 claude-sonnet-4-6
    ANTHROPIC_VERSION        默认 2023-06-01
    ANTHROPIC_SYSTEM         可选 system prompt
    ANTHROPIC_INSECURE       默认 =1（不校验 SSL，适配自签证书代理）；设 =0 启用校验
    HTTPS_PROXY / HTTP_PROXY requests 默认会读
"""
import json
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()


def stream_chat(
    messages: list[dict],
    model: str,
    system: str = "",
    base_url: str = "",
    api_key: str = "",
    api_version: str = "2023-06-01",
    verify_ssl: bool = True,
) -> tuple[str, int, int]:
    """以 SSE 流式方式请求 /v1/messages，文本实时打印，返回 (full_text, in_tokens, out_tokens)。"""
    if not api_key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY")

    url = (base_url or "https://api.anthropic.com").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    url = url + "/v1/messages"

    headers = {
        "x-api-key": api_key,
        "anthropic-version": api_version,
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    body = {
        "model": model,
        "max_tokens": 4096,
        "stream": True,
        "messages": messages,
    }
    if system:
        body["system"] = system

    chunks: list[str] = []
    in_tokens = 0
    out_tokens = 0

    with requests.post(
        url, headers=headers, json=body, stream=True,
        timeout=(10, 300), verify=verify_ssl,
    ) as resp:
        # 显式不允许重定向自动跟随，便于看到 302 这类问题
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            raise RuntimeError(f"被重定向到 {resp.headers.get('Location')}（status={resp.status_code}）")

        # 解析 SSE：每条事件以 "event: X\n data: {...}\n\n" 形式
        current_event = ""
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw == "":
                current_event = ""
                continue
            if raw.startswith("event:"):
                current_event = raw[6:].strip()
                continue
            if not raw.startswith("data:"):
                continue
            data_str = raw[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                ev = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            etype = current_event or ev.get("type", "")
            if etype == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    print(text, end="", flush=True)
                    chunks.append(text)
            elif etype == "message_start":
                usage = ev.get("message", {}).get("usage", {}) or {}
                in_tokens = usage.get("input_tokens", 0) or 0
            elif etype == "message_delta":
                usage = ev.get("usage", {}) or {}
                out_tokens = usage.get("output_tokens", out_tokens) or out_tokens
            elif etype == "error":
                err = ev.get("error", {})
                raise RuntimeError(f"API error: {err}")

    print()
    return "".join(chunks), in_tokens, out_tokens


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    api_version = os.getenv("ANTHROPIC_VERSION", "2023-06-01")
    system = os.getenv("ANTHROPIC_SYSTEM", "")
    # 默认忽略 SSL 校验，便于通过自签证书代理；设 ANTHROPIC_INSECURE=0 重新开启
    verify_ssl = os.getenv("ANTHROPIC_INSECURE", "1").lower() in ("0", "false", "no")

    if not verify_ssl:
        # 关 SSL 校验时同步压掉 InsecureRequestWarning
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    common = dict(model=model, system=system, base_url=base_url,
                  api_key=api_key, api_version=api_version, verify_ssl=verify_ssl)

    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
        _, i, o = stream_chat([{"role": "user", "content": user_text}], **common)
        print(f"\n[usage] input={i} output={o}")
        return

    history: list[dict] = []
    print(f"[model] {model}  base_url={base_url or 'default'}  (Ctrl-C 退出)")
    while True:
        try:
            user_text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        history.append({"role": "user", "content": user_text})
        try:
            reply, i, o = stream_chat(history, **common)
        except Exception as e:
            print(f"[error] {e}")
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        print(f"[usage] input={i} output={o}")


if __name__ == "__main__":
    # 本地调试用，注意不要提交到 git
    # os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."
    # os.environ["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"
    main()
