# -*- coding: utf-8 -*-
"""S03/H18 事件观察推送（默认 dry-run，--send 才发飞书）。

数据：portal/static/h18_watch.json（由 selection_daily 的 forecast_watch 生成）
边界：只提醒、不交易；默认 dry-run；--send 走既有 news_collector/send_feishu 通道。
用法：python tools/research/h18_watch_push.py [--send] [--refresh]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"
WATCH = STATIC / "h18_watch.json"
KIND = "h18_watch"

sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))
from safe_json import backup_then_atomic_write  # noqa: E402


def refresh() -> None:
    """重跑每日任务，从其 forecast_watch 刷新 h18_watch.json。"""
    daily = REPO / "tools" / "research" / "selection_daily.py"
    subprocess.run([sys.executable, "-X", "utf8", str(daily)], capture_output=True, text=True, timeout=300)
    d = json.loads((STATIC / "selection_daily.json").read_text(encoding="utf-8"))
    watch = d.get("forecast_watch") or {}
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "date": d.get("date"), "items": watch.get("items") or []}
    backup_then_atomic_write(WATCH, payload)


def build_text(data: dict) -> str:
    items = data.get("items") or []
    lines = [f"【H18 业绩预告修订观察】{data.get('date', '')}",
             "属性：事件观察，不构成买入信号（改善/恶化组 20 日均负、价差不显著）", ""]
    for w in items[:12]:
        lines.append(f"- {w.get('ts_code')} {w.get('type','')} | {w.get('direction')} {w.get('delta_pp')}pp | "
                     f"{w.get('ann_date')} | {str(w.get('summary') or '')[:40]}")
    if len(items) > 12:
        lines.append(f"… 共 {len(items)} 条（详见 /selection.html）")
    lines += ["", "研究输出，不构成投资建议。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--refresh", action="store_true", help="先刷新 h18_watch.json")
    args = ap.parse_args(argv)
    if args.refresh or not WATCH.exists():
        refresh()
    data = json.loads(WATCH.read_text(encoding="utf-8"))
    if not data.get("items"):
        print(json.dumps({"ok": False, "reason": "今日无修订事件"}, ensure_ascii=False))
        return 0
    text = build_text(data)
    if not args.send:
        print(json.dumps({"ok": True, "mode": "dry-run", "date": data.get("date"), "items": len(data["items"]),
                          "chars": len(text), "sent": False, "preview": text[:800]}, ensure_ascii=False, indent=1))
        return 0
    news = REPO / "news_collector"
    sys.path.insert(0, str(news))
    try:
        from send_feishu import send_text
    except ImportError as exc:
        print(json.dumps({"ok": False, "reason": f"发送器不可用：{exc}"}, ensure_ascii=False))
        return 2
    ok = bool(send_text(text, kind=KIND))
    print(json.dumps({"ok": ok, "mode": "send", "items": len(data["items"]), "chars": len(text), "sent": ok},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
