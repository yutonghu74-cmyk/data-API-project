"""
独立运行的 Anthropic API 调用测试脚本。

逻辑来源：server.py 的 /chat 端点（仅 Anthropic 原生分支）。
不依赖 FastAPI / 数据库 / 鉴权，便于单独调试 SDK 行为。

用法：
    export ANTHROPIC_API_KEY=sk-ant-...
    # 可选：export ANTHROPIC_BASE_URL=https://api.anthropic.com
    python test.py "你好，介绍一下你自己"
    python test.py                          # 不传参则进入交互模式

环境变量：
    ANTHROPIC_API_KEY   必填
    ANTHROPIC_BASE_URL  可选，默认走 SDK 默认地址
    ANTHROPIC_MODEL     可选，默认 claude-sonnet-4-6
    ANTHROPIC_SYSTEM    可选，system prompt
"""
import os
import sys
import anthropic


def stream_chat(messages: list[dict], model: str, system: str = "", base_url: str = "") -> tuple[str, int, int]:
    """流式调用 Anthropic，文本实时打印到 stdout，返回 (full_text, input_tokens, output_tokens)。"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY 环境变量")

    client_kwargs = {"api_key": api_key}
    normalized = (base_url or "").rstrip("/")
    if normalized:
        # 与 server.py 行为一致：base_url 以 /v1 结尾时去掉
        client_kwargs["base_url"] = normalized[:-3].rstrip("/") if normalized.endswith("/v1") else normalized

    client = anthropic.Anthropic(**client_kwargs)

    kwargs = {"model": model, "max_tokens": 4096, "messages": messages}
    if system:
        kwargs["system"] = system

    chunks: list[str] = []
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            chunks.append(text)
        usage = stream.get_final_message().usage
    print()
    return "".join(chunks), usage.input_tokens, usage.output_tokens


def main():
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    system = os.getenv("ANTHROPIC_SYSTEM", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "")

    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
        messages = [{"role": "user", "content": user_text}]
        _, i, o = stream_chat(messages, model=model, system=system, base_url=base_url)
        print(f"\n[usage] input={i} output={o}")
        return

    # 交互模式：多轮对话
    history: list[dict] = []
    print(f"[model] {model}  (Ctrl-C 退出)")
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
            reply, i, o = stream_chat(history, model=model, system=system, base_url=base_url)
        except Exception as e:
            print(f"[error] {e}")
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        print(f"[usage] input={i} output={o}")


if __name__ == "__main__":
    main()
