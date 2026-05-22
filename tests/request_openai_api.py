"""
用 openai 官方 SDK 调用 Chat Completions API。

适用范围：
    - OpenAI 官方 (api.openai.com)
    - 一步API / New API / OneAPI 等 OpenAI 兼容代理

用法:
    python request_openai_api.py "你好"
    python request_openai_api.py "描述这张图" --image ./photo.jpg
    python request_openai_api.py "对比这两张" -i a.png -i https://x.com/b.jpg
    python request_openai_api.py             # 交互模式 (可用 `/img <路径或URL> <文字>` 带图)

环境变量:
    OPENAI_API_KEY        必填
    OPENAI_BASE_URL       默认走 SDK 默认 (https://api.openai.com/v1)
    OPENAI_MODEL          默认 gpt-4o-mini（须支持 vision 才能看图）
    OPENAI_SYSTEM         可选 system prompt
    OPENAI_INSECURE       =1 时不校验 SSL（自签证书代理场景）
    OPENAI_HTTP_PROXY     可选；优先于 HTTPS_PROXY/HTTP_PROXY
    HTTPS_PROXY / HTTP_PROXY  httpx 默认会读
"""
import argparse
import base64
import mimetypes
import os
import sys
from pathlib import Path
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


def _build_image_block(src: str) -> dict:
    """src 可以是本地文件路径，或 http(s)/data: URL。返回 OpenAI vision 格式的 image_url 块。"""
    if src.startswith(("http://", "https://", "data:")):
        return {"type": "image_url", "image_url": {"url": src}}
    path = Path(src).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {src}")
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _build_user_message(text: str, image_sources: list[str]) -> dict:
    """根据是否带图，组装单条 user message。无图时退化为纯字符串 content。"""
    if not image_sources:
        return {"role": "user", "content": text}
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for src in image_sources:
        content.append(_build_image_block(src))
    return {"role": "user", "content": content}


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
    parser = argparse.ArgumentParser(description="OpenAI Chat 测试脚本（支持文本 + 图片）")
    parser.add_argument("prompt", nargs="*", help="用户文本消息")
    parser.add_argument("--image", "-i", action="append", default=[],
                        help="图片路径或 URL，可重复使用以传多张")
    args = parser.parse_args()

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

    if args.prompt or args.image:
        user_text = " ".join(args.prompt)
        user_msg = _build_user_message(user_text, args.image)
        _, pt, ct = stream_chat(client, [user_msg], model=model, system=system)
        print(f"\n[usage] prompt={pt} completion={ct}")
        return

    history: list[dict] = []
    print(f"[model] {model}  base_url={base_url or 'default'}  (Ctrl-C 退出)")
    print("提示：交互模式可用 `/img <路径或URL> <文字>` 带图，例如 `/img ./a.png 描述这张图`")
    while True:
        try:
            user_text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue

        # 解析 /img 命令：/img <src> [text...]
        img_sources: list[str] = []
        if user_text.startswith("/img "):
            parts = user_text[5:].split(maxsplit=1)
            if parts:
                img_sources.append(parts[0])
                user_text = parts[1] if len(parts) > 1 else ""

        try:
            user_msg = _build_user_message(user_text, img_sources)
        except FileNotFoundError as e:
            print(f"[error] {e}")
            continue

        history.append(user_msg)
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
    # os.environ["OPENAI_API_KEY"] = "sk-..."
    # os.environ["OPENAI_BASE_URL"] = "https://api.openai.com"
    os.environ["OPENAI_INSECURE"] = "1"  # 跳过 SSL 校验（自签证书代理）
    main()
