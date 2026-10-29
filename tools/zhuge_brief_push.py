#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zhuge_brief_push：诸葛亮晨报/晚报推送到个人飞书。

默认把快照 JSON 格式化为「两期摘要」（晨报/晚报），再进行 dry-run 或发送；
--raw 保留旧的整篇 md 直发作为回退。

边界：默认只 dry-run；--send 才调用既有 news_collector/send_feishu 通道。
不生成新数据、不改页面、不发第二份。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_DIR = REPO / "work" / "seat_brief_zhuge"
DEFAULT_KIND = "daily:zhuge_brief"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import zhuge_brief_digest as DIGEST  # noqa: E402


def latest_snapshot(out_dir: Path = DEFAULT_DIR) -> Path | None:
    files = [Path(p) for p in glob.glob(str(Path(out_dir) / "zhuge_brief_*.md"))]
    if not files:
        return None
    return sorted(files)[-1]


def latest_json(out_dir: Path = DEFAULT_DIR) -> Path | None:
    files = [Path(p) for p in glob.glob(str(Path(out_dir) / "zhuge_brief_*.json"))]
    if not files:
        return None
    return sorted(files)[-1]


def _resolve_json(snapshot: str, out_dir: Path) -> Path | None:
    if snapshot:
        p = Path(snapshot)
        if p.suffix.lower() == ".json":
            return p if p.is_file() else None
        return DIGEST.find_json_for(p) if p.is_file() else None
    return latest_json(out_dir)


def load_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("快照为空")
    if "# 诸葛亮证据快照" not in text:
        raise ValueError("快照头不符（需 # 诸葛亮证据快照）")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛亮晨报/晚报推送（两期摘要）")
    ap.add_argument("--snapshot", default="", help="快照 md/json 路径；缺省取最新")
    ap.add_argument("--out-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只校验不发送（默认）")
    ap.add_argument("--kind", default=DEFAULT_KIND)
    ap.add_argument("--edition", choices=("auto", "morning", "evening"),
                    default="auto", help="auto=按快照生成时间判断")
    ap.add_argument("--raw", action="store_true", help="回退：整篇 md 直发")
    args = ap.parse_args(argv)

    path = Path(args.snapshot) if args.snapshot else latest_snapshot(Path(args.out_dir))
    if path is None or not path.is_file():
        print("[zhuge_brief_push] 找不到快照：%s" % (args.snapshot or args.out_dir))
        return 2

    edition = args.edition
    payload = None
    if args.raw:
        try:
            text = load_text(path)
        except (OSError, ValueError) as exc:
            print("[zhuge_brief_push] 快照不可用：%s" % exc)
            return 2
    else:
        json_path = _resolve_json(args.snapshot, Path(args.out_dir))
        if json_path is None:
            print("[zhuge_brief_push] 找不到与快照同名的 JSON：%s" % path)
            return 2
        try:
            payload = DIGEST.load_payload(json_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print("[zhuge_brief_push] 快照 JSON 不可用：%s" % exc)
            return 2
        digest = DIGEST.build_digest(payload, edition)
        text = digest["text"]
        edition = digest["edition"]

    if not args.send or args.dry_run:
        out = {
            "ok": True, "mode": "dry-run", "snapshot": str(path),
            "edition": edition, "chars": len(text), "kind": args.kind,
            "raw": bool(args.raw), "sent": False,
            "preview": text[:1200],
        }
        if payload is not None:
            d = DIGEST.build_digest(payload, edition)
            out["counts"] = d["counts"]
            out["collapsed_p4"] = d["collapsed_p4"]
        print(json.dumps(out, ensure_ascii=False))
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
        "edition": edition, "chars": len(text), "kind": args.kind,
        "raw": bool(args.raw), "sent": ok,
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
