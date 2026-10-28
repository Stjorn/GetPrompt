# inspect_capture.py — 解剖一条抓到的 LLM 请求体（三协议自适应）
# 用法: uv run python analysis/inspect_capture.py <captures/proxy/请求体文件>
import json
import sys


def brief(s, n=100):
    s = s.replace("\n", "\\n")
    return s[:n] + ("..." if len(s) > n else "")


def content_shape(c):
    """消息 content 可能是字符串或块数组，返回简要形状"""
    if isinstance(c, str):
        return [f"str({len(c)}字符): {brief(c, 60)}"]
    out = []
    for b in c or []:
        t = b.get("type", "?")
        if t in ("text", "input_text", "output_text"):
            out.append(f"text({len(b.get('text',''))}字符): {brief(b.get('text',''), 60)}")
        elif t == "thinking":
            out.append(f"thinking({len(b.get('thinking',''))}字符)")
        elif t == "tool_use":
            out.append(f"tool_use(name={b.get('name')}, input={brief(json.dumps(b.get('input',{}), ensure_ascii=False), 40)})")
        elif t == "tool_result":
            inner = b.get("content")
            size = len(inner) if isinstance(inner, str) else len(json.dumps(inner, ensure_ascii=False))
            out.append(f"tool_result({size}字符)")
        else:
            out.append(f"{t}({len(json.dumps(b, ensure_ascii=False))}字符)")
    return out


def conversation(body):
    """返回 Anthropic/Chat 的 messages 或 Responses 的 input。"""
    if isinstance(body.get("messages"), list):
        return "messages", body["messages"]
    if isinstance(body.get("input"), list):
        return "input", body["input"]
    return "messages/input", []


def system_blocks(body, sequence_name, sequence):
    """统一收集顶层 system 和消息中的 system/developer 文本。"""
    out = []
    system = body.get("system")
    if isinstance(system, str):
        out.append(("system", system, None))
    elif isinstance(system, list):
        for i, block in enumerate(system):
            if isinstance(block, dict):
                out.append((f"system[{i}]", block.get("text", ""), block.get("cache_control")))
            else:
                out.append((f"system[{i}]", str(block), None))
    if isinstance(body.get("instructions"), str):
        out.append(("instructions", body["instructions"], None))
    for i, message in enumerate(sequence):
        if not isinstance(message, dict) or message.get("role") not in ("system", "developer"):
            continue
        content = message.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        for j, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text"):
                out.append((
                    f"{sequence_name}[{i}].content[{j}] role={message['role']}",
                    block.get("text", ""), None,
                ))
    return out


def tool_name(tool):
    if not isinstance(tool, dict):
        return "?"
    if isinstance(tool.get("function"), dict):
        return tool["function"].get("name", "?")
    return tool.get("name", tool.get("type", "?"))


def main():
    path = sys.argv[1]
    body = json.load(open(path, encoding="utf-8"))
    sequence_name, msgs = conversation(body)
    print(f"文件: {path} (代理抓包格式)\n")

    print(f"== request.body 字段 ==")
    for k in body:
        if k not in ("system", "instructions", "tools", "messages", "input"):
            print(f"  {k}: {brief(json.dumps(body[k], ensure_ascii=False), 90)}")

    systems = system_blocks(body, sequence_name, msgs)
    print(f"\n== system / developer 上下文 ({len(systems)} 块) ==")
    for location, txt, cc in systems:
        print(f"  [{location}] {len(txt)}字符 cache_control={cc or '-'}")
        print(f"      开头: {brief(txt, 70)}")
        print(f"      结尾: ...{brief(txt[-70:], 70)}")

    tools = body.get("tools", [])
    print(f"\n== tools ({len(tools)} 个) ==")
    print("  " + ", ".join(tool_name(t) for t in tools))

    print(f"\n== {sequence_name} ({len(msgs)} 条) ==")
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            print(f"     [{i}] unknown: {brief(json.dumps(m, ensure_ascii=False), 80)}")
            continue
        if "content" not in m and m.get("type") != "message":
            print(f"     [{i}] {m.get('type', '?')}: {brief(json.dumps(m, ensure_ascii=False), 100)}")
            continue
        for line in content_shape(m.get("content")):
            tag = ">>" if i == len(msgs) - 1 else "  "
            print(f"  {tag} [{i}] {m.get('role', m.get('type', '?'))}: {line}")


if __name__ == "__main__":
    main()
