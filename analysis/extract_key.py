# extract_key.py — 抽取三协议请求体关键内容: 小块全文, 大块给标题结构+首尾
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))


def conversation():
    if isinstance(body.get("messages"), list):
        return "messages", body["messages"]
    if isinstance(body.get("input"), list):
        return "input", body["input"]
    return "messages/input", []


def text_blocks(content):
    if isinstance(content, str):
        return [(None, "str", content)]
    return [
        (i, block.get("type"), block.get("text", ""))
        for i, block in enumerate(content or [])
        if isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text")
    ]


def system_entries(sequence_name, messages):
    entries = []
    system = body.get("system")
    if isinstance(system, str):
        entries.append(("system", system, None))
    elif isinstance(system, list):
        for i, block in enumerate(system):
            if isinstance(block, dict):
                entries.append((f"system[{i}]", block.get("text", ""), block.get("cache_control")))
            else:
                entries.append((f"system[{i}]", str(block), None))
    if isinstance(body.get("instructions"), str):
        entries.append(("instructions", body["instructions"], None))
    for i, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in ("system", "developer"):
            continue
        for block_idx, block_type, text in text_blocks(message.get("content")):
            suffix = "" if block_idx is None else f".content[{block_idx}]"
            entries.append((
                f"{sequence_name}[{i}]{suffix} role={message['role']} type={block_type}",
                text, None,
            ))
    return entries


def tool_info(tool):
    if not isinstance(tool, dict):
        return "?", ""
    item = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    desc = (item.get("description") or "").split("\n")[0][:70]
    return item.get("name", tool.get("type", "?")), desc


def show_text(txt, full_limit=200, head_limit=400):
    if len(txt) <= full_limit:
        print(txt)
        return
    heads = [l.strip() for l in txt.split("\n") if l.strip().startswith("#")]
    if heads:
        print("[标题结构]")
        for h in heads:
            print("   " + h)
    print(f"[开头 {head_limit} 字]")
    print(txt[:head_limit])
    print(f"[结尾 {head_limit} 字]")
    print(txt[-head_limit:])


sequence_name, messages = conversation()

print("=" * 20, "SYSTEM / DEVELOPER 上下文", "=" * 20)
for location, txt, cache_control in system_entries(sequence_name, messages):
    print(f"\n--- {location} {len(txt)}字符 cache_control={cache_control or '-'}")
    show_text(txt)

print("\n" + "=" * 25, "TOOLS", "=" * 25)
for t in body.get("tools", []):
    name, desc = tool_info(t)
    print(f"  {name:<24} {desc}")

print("\n" + "=" * 22, sequence_name.upper(), "=" * 22)
for i, m in enumerate(messages):
    if not isinstance(m, dict):
        print(f"\n--- {sequence_name}[{i}] 非对象: {json.dumps(m, ensure_ascii=False)[:200]}")
        continue
    blocks = text_blocks(m.get("content"))
    if blocks:
        for j, block_type, txt in blocks:
            suffix = "" if j is None else f"#{j}"
            print(
                f"\n--- {sequence_name}[{i}] {m.get('role', m.get('type', '?'))} "
                f"{block_type}块{suffix} ({len(txt)}字符)"
            )
            show_text(txt, full_limit=700, head_limit=400)
    else:
        print(
            f"\n--- {sequence_name}[{i}] {m.get('role', m.get('type', '?'))} 非文本项: "
            f"{json.dumps(m, ensure_ascii=False)[:200]}"
        )
