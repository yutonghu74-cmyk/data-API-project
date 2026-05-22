"""
用 openai 官方 SDK 调用 Chat Completions API。

适用范围：
    - OpenAI 官方 (api.openai.com)
    - 一步API / New API / OneAPI 等 OpenAI 兼容代理

用法:
    python request_openai_api.py "你好"
    python request_openai_api.py            # 交互模式

环境变量:
    OPENAI_API_KEY        必填
    OPENAI_BASE_URL       默认走 SDK 默认 (https://api.openai.com/v1)
    OPENAI_MODEL          默认 gpt-4o-mini
    OPENAI_SYSTEM         可选 system prompt
    OPENAI_INSECURE       =1 时不校验 SSL（自签证书代理场景）
    OPENAI_HTTP_PROXY     可选；优先于 HTTPS_PROXY/HTTP_PROXY
    HTTPS_PROXY / HTTP_PROXY  httpx 默认会读
"""
import os
import sys
import httpx
from openai import OpenAI
from dotenv import load_dotenv

# Windows / 老终端 stdout 可能不是 UTF-8，强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, Exception):
    pass

load_dotenv()


def _fix_mojibake(s: str) -> str:
    """部分代理把 UTF-8 当 Latin-1 解码再用 UTF-8 编码送出（中文变 'ææ'），反向修复。"""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _build_client(api_key: str, base_url: str, verify_ssl: bool, proxy: str) -> OpenAI:
    if not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY")

    kwargs = {"api_key": api_key}
    if base_url:
        # 与 server.py 一致：base_url 不以 /v1 结尾时自动补
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = normalized + "/v1"
        kwargs["base_url"] = normalized

    # 需要自定义 SSL / proxy 时传入 httpx.Client
    if not verify_ssl or proxy:
        client_kwargs = {"verify": verify_ssl}
        if proxy:
            client_kwargs["proxy"] = proxy
        kwargs["http_client"] = httpx.Client(**client_kwargs)

    return OpenAI(**kwargs)


def stream_chat(
    client: OpenAI,
    messages: list[dict],
    model: str,
    system: str = "",
) -> tuple[str, int, int]:
    """流式调用 chat.completions.create，文本实时打印，返回 (full_text, prompt_tokens, completion_tokens)。"""
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)

    stream = client.chat.completions.create(
        model=model,
        messages=oai_messages,
        max_tokens=4096,
        stream=True,
        # 让最后一个 chunk 携带 usage（OpenAI 官方 + 多数兼容代理支持）
        stream_options={"include_usage": True},
    )

    chunks: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None) if delta else None
            if piece:
                piece = _fix_mojibake(piece)
                print(piece, end="", flush=True)
                chunks.append(piece)
        usage = getattr(chunk, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens) or prompt_tokens
            completion_tokens = getattr(usage, "completion_tokens", completion_tokens) or completion_tokens

    print()
    return "".join(chunks), prompt_tokens, completion_tokens


def main():
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    system = os.getenv("OPENAI_SYSTEM", "")
    verify_ssl = os.getenv("OPENAI_INSECURE", "").lower() not in ("1", "true", "yes")
    proxy = os.getenv("OPENAI_HTTP_PROXY", "")  # 不设的话 httpx 自动读 HTTPS_PROXY

    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    client = _build_client(api_key=api_key, base_url=base_url,
                           verify_ssl=verify_ssl, proxy=proxy)

    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
        _, pt, ct = stream_chat(client, [{"role": "user", "content": user_text}],
                                model=model, system=system)
        print(f"\n[usage] prompt={pt} completion={ct}")
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
            reply, pt, ct = stream_chat(client, history, model=model, system=system)
        except Exception as e:
            print(f"[error] {e}")
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        print(f"[usage] prompt={pt} completion={ct}")


if __name__ == "__main__":
    # 本地调试用，注意不要提交到 git
    main()
