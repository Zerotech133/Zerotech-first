# -*- coding: utf-8 -*-
"""学习卡每日推送（默认 dry-run，--send 走既有飞书通道）。

数据：portal/static/kb_learning_cards.json（learning_cards.py 生成，含 20 张卡 + daily）
用法：python tools/kb_corpus/learning_card_push.py [--n 5] [--send] [--refresh]
边界：只推送学习内容；默认 dry-run；--send 复用 news_collector/send_feishu。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"
CARDS = STATIC / "kb_learning_cards.json"
KIND = "learning_card"


def refresh() -> None:
    subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/kb_corpus/learning_cards.py"), "--n", "20"],
                   capture_output=True, text=True, timeout=300)


def build_text(data: dict, n: int = 5) -> str:
    cards = (data.get("cards") or [])[:n]
    lines = ["【今日学习卡】来自研报/书籍知识库", ""]
    for i, c in enumerate(cards, 1):
        lines.append(f"{i}. Q：{c.get('q')}")
        lines.append(f"   A：{c.get('a')}")
        lines.append(f"   出处：{c.get('source')}")
        lines.append("")
    lines += ["——", "知识库 · 零界（内部学习材料，引用请带出处）"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    if args.refresh or not CARDS.exists():
        refresh()
    data = json.loads(CARDS.read_text(encoding="utf-8"))
    text = build_text(data, args.n)
    if not args.send:
        print(json.dumps({"ok": True, "mode": "dry-run", "cards": min(args.n, len(data.get("cards") or [])),
                          "total_units": data.get("total_units"), "chars": len(text), "sent": False,
                          "preview": text[:800]}, ensure_ascii=False, indent=1))
        return 0
    news = REPO / "news_collector"
    sys.path.insert(0, str(news))
    try:
        from send_feishu import send_text
    except ImportError as exc:
        print(json.dumps({"ok": False, "reason": f"发送器不可用：{exc}"}, ensure_ascii=False))
        return 2
    ok = bool(send_text(text, kind=KIND))
    print(json.dumps({"ok": ok, "mode": "send", "chars": len(text), "sent": ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
