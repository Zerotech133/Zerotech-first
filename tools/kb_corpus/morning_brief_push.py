# -*- coding: utf-8 -*-
"""零界知识早报：学习卡(5张) + H18 预告修订观察(前8条) 合并为一条早报（默认 dry-run）。

数据：portal/static/kb_learning_cards.json + portal/static/h18_watch.json
用法：python tools/kb_corpus/morning_brief_push.py [--send] [--refresh]
边界：默认 dry-run；--send 复用 news_collector/send_feishu；只提醒不构成投资建议。
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
WATCH = STATIC / "h18_watch.json"
KIND = "kb_morning_brief"


def refresh() -> None:
    subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/kb_corpus/learning_cards.py"), "--n", "20"],
                   capture_output=True, text=True, timeout=300)
    subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/research/selection_daily.py")],
                   capture_output=True, text=True, timeout=300)
    d = json.loads((STATIC / "selection_daily.json").read_text(encoding="utf-8"))
    w = d.get("forecast_watch") or {}
    WATCH.write_text(json.dumps({"generated_at": d.get("generated_at"), "date": d.get("date"),
                                 "items": w.get("items") or []}, ensure_ascii=False, indent=1), encoding="utf-8")


def build_text(cards: dict, watch: dict, n_cards: int = 5, n_watch: int = 8) -> str:
    lines = ["【零界知识早报】学习 + 观察", ""]
    lines.append("📚 今日学习卡")
    for i, c in enumerate((cards.get("cards") or [])[:n_cards], 1):
        lines.append(f"{i}. Q：{c.get('q')}")
        lines.append(f"   A：{c.get('a')}")
        lines.append(f"   [{c.get('topic', '')}] 出处：{c.get('source')}")
    lines.append("")
    items = watch.get("items") or []
    lines.append(f"📡 H18 预告修订观察（{watch.get('date', '')}，共 {len(items)} 条）")
    if items:
        for w in items[:n_watch]:
            lines.append(f"- {w.get('ts_code')} {w.get('type', '')} | {w.get('direction')} "
                         f"{w.get('delta_pp')}pp | {w.get('ann_date')}")
        if len(items) > n_watch:
            lines.append(f"  … 其余 {len(items) - n_watch} 条见 /selection.html")
    else:
        lines.append("- 今日无修订事件")
    lines += ["", "——", "属性：学习材料/事件提醒，不构成投资建议；引用请带出处。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    if args.refresh or not CARDS.exists() or not WATCH.exists():
        refresh()
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    watch = json.loads(WATCH.read_text(encoding="utf-8"))
    text = build_text(cards, watch)
    if not args.send:
        print(json.dumps({"ok": True, "mode": "dry-run", "cards": min(5, len(cards.get("cards") or [])),
                          "watch_items": len(watch.get("items") or []), "chars": len(text), "sent": False,
                          "preview": text[:900]}, ensure_ascii=False, indent=1))
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
