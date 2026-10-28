# jev_pilot.py — Jev 逐块分类试点：role=user 消息的每个 text 块是什么？
# 用法:
#   指定捕获: uv run python analysis/jev_pilot.py captures/proxy/<session>/<request>.user.json
#   最新捕获: uv run python analysis/jev_pilot.py --latest
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROXY_DIR = ROOT / "captures" / "proxy"   # 反代落盘位置
JEV_OUT = ROOT / "captures" / "jev"       # Jev 判定结果落盘位置
STATE_TRUNCATE = 8000  # 单块喂给 Jev 的最大字符数，防超长块

CLASSES = ["user_input", "template_context", "tool_output", "other_noise"]
CLASS_DESC = {
    "user_input": "用户通过输入框亲手输入的真实请求内容（问题、指令、粘贴的文本）",
    "template_context": "Agent 框架自动注入的上下文/提醒/声明，如 <system-reminder>、git 署名规则、环境信息、附件说明",
    "tool_output": "工具/命令执行结果的回传内容",
    "other_noise": "其他非用户内容：时间戳、会话ID、计数器等机器生成字段",
}


# ---------- 环境与数据 ----------

def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def walk_user_blocks(user_json: dict) -> list:
    """从 .user.json 里拿出所有 user 消息的 text 块，带定位信息。"""
    msgs = user_json.get("user_messages", [])
    blocks = []
    for mi, m in enumerate(msgs):
        c = m.get("content")
        texts = [(None, c)] if isinstance(c, str) else [
            (bi, b.get("text", "")) for bi, b in enumerate(c or [])
            if isinstance(b, dict) and b.get("type") in ("text", "input_text")
        ]
        for bi, text in texts:
            blocks.append({
                "msg_idx": mi, "block_idx": bi,
                "chars": len(text), "text": text,
            })
    return blocks


def latest_user_json() -> pathlib.Path:
    files = sorted(PROXY_DIR.rglob("*.user.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        sys.exit("captures\\proxy\\ 下没有 .user.json，先抓一次包")
    return files[-1]


# ---------- Jev 调用（线协议唯一入口） ----------

def build_request(blocks: list, model: str) -> dict:
    """批处理：一次调用判完所有块。state = 带 [块N] 标记的全部块；
    每块一个四选一问题（Jev 对同一 state 的多问题并行评估、互不干扰）。"""
    parts, questions = [], {}
    for i, b in enumerate(blocks, 1):
        text = b["text"][:STATE_TRUNCATE]
        if len(b["text"]) > STATE_TRUNCATE:
            text += "\n...(截断)"
        parts.append(f"[块{i}]\n{text}")
        questions[f"block_{i}"] = {
            "type": "choice",
            "instructions": f"只针对[块{i}]这一段文本判断属于哪一类，其余块仅供对比参考。",
            "criteria": {c: CLASS_DESC[c] for c in CLASSES},
        }
    return {
        "model": model,
        "state": "\n\n".join(parts),
        "questions": questions,
    }


def call_jev(req_body: dict, env: dict, timeout: int = 60) -> dict:
    # Jev 是 decisions 模型，走专用端点 /api/alpha/decisions（chat/completions 会 400）
    root = env["OPENROUTER_BASE_URL"].split("/api")[0]
    url = root + "/api/alpha/decisions"
    req = urllib.request.Request(
        url,
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {env['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"[HTTP {e.code}] 线协议可能需要调整，响应原文:\n{detail}")
    answers = resp.get("answers", {})
    if not answers:
        sys.exit(f"[解析失败] 响应里没有 answers:\n{json.dumps(resp, ensure_ascii=False)[:800]}")
    return answers  # {block_1: {type, choice, confidence, probabilities}, ...}


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help=".user.json 文件路径")
    ap.add_argument("--latest", action="store_true", help="用 captures\\ 下最新的 .user.json")
    ap.add_argument("--limit", type=int, help="最多处理几个块")
    args = ap.parse_args()

    path = pathlib.Path(args.latest and latest_user_json() or args.path or latest_user_json())
    user_json = json.loads(path.read_text(encoding="utf-8"))
    blocks = walk_user_blocks(user_json)
    if args.limit:
        blocks = blocks[: args.limit]
    if not blocks:
        sys.exit(f"[无文本块] {path.name} 中没有可供 Jev 判断的 text/input_text 块")

    print(f"试点文件: {path.name}  |  user 消息数 {user_json.get('user_message_count')}  |  待判块数 {len(blocks)}\n")

    env = load_env()
    req = build_request(blocks, env.get("JEV_MODEL", "typesafe/jev-1.13"))

    t0 = time.perf_counter()
    answers = call_jev(req, env)
    ms = (time.perf_counter() - t0) * 1000
    print(f"[调用] 1 次 HTTP / {len(blocks)} 个问题并行，耗时 {ms:.0f} ms\n")

    results = []
    for i, b in enumerate(blocks, 1):
        verdict = answers.get(
            f"block_{i}", {"choice": "(missing)", "probabilities": {}, "confidence": None}
        )
        results.append({**b, "verdict": verdict})
        probs = verdict.get("probabilities", {})
        print(f"[msg{b['msg_idx']}.blk{b['block_idx']}] "
              f"{b['text'][:40]!r}... → {verdict.get('choice')}  "
              f"P={probs}  conf={verdict.get('confidence')}")

    # 汇总：判为 user_input 的块即提取结果（暂无任何护栏，纯 Jev 判断）
    extracted = [r for r in results if r["verdict"].get("choice") == "user_input"]
    print("\n== 提取结果（被判 user_input 的块）==")
    for r in extracted:
        print(f"  {r['text'][:200]!r}")
    out = JEV_OUT / f"jev_pilot_{time.strftime('%H%M%S')}.json"
    JEV_OUT.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"source": path.name, "results": [
            {k: r[k] for k in ("msg_idx", "block_idx", "chars", "verdict")}
            | {"text": r["text"]} for r in results
        ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已存: {out.name}")


if __name__ == "__main__":
    main()
