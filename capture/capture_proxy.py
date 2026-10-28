# capture_proxy.py — 本地反向代理：把 Agent 发给 LLM 的完整请求体落盘
# 用法: python capture_proxy.py <UPSTREAM> [PORT]   (默认监听 127.0.0.1:8099)
#   UPSTREAM 例: http://127.0.0.1:15721 (GLM桥) | https://api.anthropic.com
#   然后让目标 Agent 把 base URL 指到 http://127.0.0.1:8099 即可
import json
import os
import pathlib
import re
import sys
import time

from aiohttp import web, ClientSession

# 命令行参数: python capture_proxy.py <UPSTREAM> [PORT] [FALLBACK_SESSION]
UPSTREAM = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.environ.get("UPSTREAM", "https://api.anthropic.com")
)
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", "8099"))
FALLBACK_SESSION = (
    sys.argv[3] if len(sys.argv) > 3 else os.environ.get("CAPTURE_SESSION", "")
)
OUT = pathlib.Path(__file__).resolve().parent.parent / "captures" / "proxy"  # 项目根/captures/proxy
OUT.mkdir(parents=True, exist_ok=True)
_counter = 0

# 各 Agent 已验证会随请求发送稳定的会话 ID。按明确 header 分目录，避免跨会话平铺混杂。
SESSION_HEADERS = (
    ("x-deepseek-harness-session-id", "deepseek"),
    ("x-claude-code-session-id", "claude"),
    ("x-opencode-session-id", "opencode"),
    ("session-id", "codex"),
    ("thread-id", "codex"),
)
SENSITIVE_HEADERS = {
    "authorization", "x-api-key", "acl-token", "cookie",
    "proxy-authorization",
}


def safe_folder_name(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._-")
    return (safe or "unnamed")[:120]


def session_folder(headers) -> str:
    """从请求头取 Agent/session，并转成安全的 Windows 目录名。"""
    for header, agent in SESSION_HEADERS:
        session_id = headers.get(header)
        if not session_id:
            continue
        return f"{agent}_{safe_folder_name(session_id)}"
    if FALLBACK_SESSION:
        return safe_folder_name(FALLBACK_SESSION)
    return "_unknown"


async def proxy(request: web.Request) -> web.StreamResponse:
    global _counter
    _counter += 1
    body = await request.read()

    # 请求体原始字节直接存，编码/格式问题留给后续分析
    stamp = time.strftime("%H%M%S")
    session_dir = OUT / session_folder(request.headers)
    session_dir.mkdir(parents=True, exist_ok=True)
    base = session_dir / f"{stamp}_{_counter:03d}_{request.path.strip('/').replace('/', '_')}"
    base.write_bytes(body)

    # 元信息侧车文件；authorization 落盘前抹掉，避免 key 泄漏
    meta = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": request.method,
        "path": request.path,
        "query": request.query_string,
        "capture_session": session_dir.name,
        "headers": {
            k: ("<redacted>" if k.lower() in SENSITIVE_HEADERS else v)
            for k, v in request.headers.items()
        },
        "upstream": UPSTREAM,
    }
    base.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 单独落盘 role=user 的消息（围栏核心关注对象）：
    #   user_messages    — 原样的 user 角色消息（含 tool_result 块，忠实版）
    #   user_text_blocks — 其中纯 text 块的文本（剥掉 tool_result，用户输入提炼版）
    try:
        parsed = json.loads(body)
        user_msgs = [m for m in parsed.get("messages", []) if m.get("role") == "user"]
        # Responses 协议(Codex 等):对话在 input[] 里,没有 messages 字段
        if not user_msgs and isinstance(parsed.get("input"), list):
            user_msgs = [m for m in parsed["input"]
                         if isinstance(m, dict) and m.get("role") == "user"]
        if user_msgs:
            text_blocks = []
            for m in user_msgs:
                c = m.get("content")
                if isinstance(c, str):
                    text_blocks.append(c)
                else:
                    # text=Anthropic/Chat协议块, input_text=Responses协议块
                    text_blocks += [
                        b.get("text", "") for b in c or []
                        if isinstance(b, dict) and b.get("type") in ("text", "input_text")
                    ]
            base.with_suffix(".user.json").write_text(
                json.dumps({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": str(base.relative_to(OUT)),
                    "user_message_count": len(user_msgs),
                    "user_messages": user_msgs,
                    "user_text_blocks": text_blocks,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # 非 JSON 请求体（如探活）不生成 user 文件
    print(
        f"[captured] #{_counter} {request.method} {request.path} "
        f"-> {base.relative_to(OUT)}",
        flush=True,
    )

    # 不转发逐跳头；Accept-Encoding 也交给 aiohttp 自行协商，确保它能解码后再流式回传。
    skipped_headers = {
        "host", "content-length", "connection", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te", "trailer",
        "transfer-encoding", "upgrade", "accept-encoding",
    }
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in skipped_headers
    }
    async with ClientSession() as s:
        try:
            upstream = await s.request(
                request.method, UPSTREAM + request.path,
                params=request.query, data=body, headers=headers,
            )
        except Exception as exc:
            print(
                f"[upstream-error] {request.method} {request.path}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return web.json_response(
                {"error": "capture proxy upstream failure", "type": type(exc).__name__},
                status=502,
            )

        resp = web.StreamResponse(
            status=upstream.status,
            headers={"content-type": upstream.headers.get("content-type", "")},
        )
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
        except Exception as exc:
            print(
                f"[stream-error] {request.method} {request.path}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            resp.force_close()
        return resp


def main():
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", proxy)
    print(f"listening on http://127.0.0.1:{PORT} -> {UPSTREAM}", flush=True)
    web.run_app(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
