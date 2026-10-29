#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zhuge_brief_push：把诸葛亮晨报快照推送到个人飞书。

边界：默认只 dry-run；--send 才调用既有 news_collector/send_feishu 通道。
不生成新数据、不改页面、不发第二份。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_DIR = REPO / "work" / "seat_brief_zhuge"
DEFAULT_KIND = "daily:zhuge_brief"


def latest_snapshot(out_dir: Path = DEFAULT_DIR) -> Path | None:
    files = [Path(p) for p in glob.glob(str(Path(out_dir) / "zhuge_brief_*.md"))]
    if not files:
        return None
    # 文件名含 YYYYMMDD-HHMMSS；按名排序避免同秒内 mtime 分辨率导致取错。
    return sorted(files)[-1]


def load_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("快照为空")
    if "# 诸葛亮证据快照" not in text:
        raise ValueError("快照头不符（需 # 诸葛亮证据快照）")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛亮晨报快照推送")
    ap.add_argument("--snapshot", default="", help="快照 md 路径；缺省取最新")
    ap.add_argument("--out-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只校验不发送（默认）")
    ap.add_argument("--kind", default=DEFAULT_KIND)
    args = ap.parse_args(argv)

    path = Path(args.snapshot) if args.snapshot else latest_snapshot(Path(args.out_dir))
    if path is None or not path.is_file():
        print("[zhuge_brief_push] 找不到快照：%s" % (args.snapshot or args.out_dir))
        return 2
    try:
        text = load_text(path)
    except (OSError, ValueError) as exc:
        print("[zhuge_brief_push] 快照不可用：%s" % exc)
        return 2

    if not args.send or args.dry_run:
        print(json.dumps({
            "ok": True, "mode": "dry-run", "snapshot": str(path),
            "chars": len(text), "kind": args.kind, "sent": False,
        }, ensure_ascii=False))
        return 0

    news = REPO / "news_collector"
    if str(news) not in sys.path:
        sys.path.insert(0, str(news))
    try:
        from send_feishu import send_text
    except ImportError as exc:
        print("[zhuge_brief_push] 发送器不可用：%s" % exc)
        return 2
    ok = bool(send_text(text, kind=args.kind))
    print(json.dumps({
        "ok": ok, "mode": "send", "snapshot": str(path),
        "chars": len(text), "kind": args.kind, "sent": ok,
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
