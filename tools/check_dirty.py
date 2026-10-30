#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脏文件看板/协议守卫（P0 #2，2026-10-28）。

背景：并行会话把跟踪文件改了不提交，长期占用主区，阻塞他人合并（B4 被卡 17h+）。
本工具只读：列出主区跟踪脏文件、年龄、最近提交者（建议 owner）。
--strict 时，超过阈值的跟踪脏文件 → exit 1（供巡检/定时任务使用）。

用法：
  python tools/check_dirty.py                 # 看板
  python tools/check_dirty.py --json          # 机器可读
  python tools/check_dirty.py --strict --max-age-hours 4   # 超时即失败
退出码：0=正常/无超时；1=有超时脏文件；2=git 异常。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# 已知的长寿脏文件白名单（并行线 WIP，登记在案；超过阈值只提示不算违规）
KNOWN_WIP = {
    "portal/m_api.py", "portal/m_links.py", "portal/push_daily_digest.py",
    "portal/test_push_daily_digest.py", "quant_backtest/kg_brief.py",
    "quant_backtest/kg_build_p2.py", "tools/datawarehouse/run_all.py",
    "tools/datawarehouse/staging_health.py", "tools/devtask.py",
}


def git_status() -> list[tuple[str, str]]:
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                        "status", "--porcelain=v1"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip() or "git status failed")
    rows = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        rows.append((code, path.replace("\\", "/")))
    return rows


def last_author(path: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%an <%ae>",
                        "--", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "").strip() or "?"


def classify(code: str) -> str:
    if "R" in code or "C" in code:
        return "renamed"
    if "D" in code:
        return "deleted"
    if "M" in code:
        return "modified"
    if "A" in code:
        return "added"
    if code.strip() == "??":
        return "untracked"
    return "other"


ESCALATE_HOURS = 168.0  # 2026-10-30 P67：>7 天长期脏文件自动升级（协议 v1.1）


def escalate_flag(age_hours, escalate_hours: float = ESCALATE_HOURS) -> bool:
    """长期脏文件标记：age > escalate_hours（0/None 关闭）。纯函数便于测试。"""
    return bool(age_hours is not None and escalate_hours
                and age_hours > escalate_hours)


def build_rows(include_untracked: bool = False,
               escalate_hours: float = ESCALATE_HOURS) -> list[dict]:
    now = time.time()
    rows = []
    for code, path in git_status():
        kind = classify(code)
        if kind == "untracked" and not include_untracked:
            continue
        full = REPO / path
        age_h = None
        if full.exists():
            age_h = round((now - full.stat().st_mtime) / 3600.0, 1)
        known = path in KNOWN_WIP
        rows.append({
            "path": path, "code": code, "kind": kind, "age_hours": age_h,
            "owner_hint": last_author(path), "known_wip": known,
            "escalate": escalate_flag(age_h, escalate_hours),
        })
    rows.sort(key=lambda r: (r["age_hours"] is None, -(r["age_hours"] or 0)))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="脏文件看板/协议守卫")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="超时脏文件 → exit 1")
    ap.add_argument("--max-age-hours", type=float, default=4.0)
    ap.add_argument("--include-untracked", action="store_true")
    ap.add_argument("--escalate-hours", type=float, default=ESCALATE_HOURS,
                    help="长期脏文件升级阈值（默认 168h=7天；0=关闭）")
    args = ap.parse_args(argv)

    try:
        rows = build_rows(args.include_untracked, args.escalate_hours)
    except RuntimeError as exc:
        print("DIRTY CHECK ERROR:", exc)
        return 2

    tracked = [r for r in rows if r["kind"] != "untracked"]
    over = [r for r in tracked if (r["age_hours"] or 0) > args.max_age_hours and not r["known_wip"]]
    known_over = [r for r in tracked if (r["age_hours"] or 0) > args.max_age_hours and r["known_wip"]]
    escalated = [r for r in tracked if r.get("escalate") and not r["known_wip"]]

    if args.json:
        print(json.dumps({"rows": rows, "over": over, "known_over": known_over,
                          "escalated": escalated, "max_age_hours": args.max_age_hours,
                          "escalate_hours": args.escalate_hours},
                         ensure_ascii=False, indent=1))
    else:
        print("DIRTY BOARD：跟踪脏文件 %d 个，未跟踪 %d 个（阈值 %.1fh）"
              % (len(tracked), len(rows) - len(tracked), args.max_age_hours))
        for r in rows:
            flag = "超过" if (r["age_hours"] or 0) > args.max_age_hours else "  "
            tag = " [WIP登记]" if r["known_wip"] else ""
            if r.get("escalate"):
                tag += " [升级>A0]"
            print("  %s %6.1fh %-9s %s%s   owner≈%s"
                  % (flag, r["age_hours"] or -1, r["kind"], r["path"], tag, r["owner_hint"]))
        if over:
            print("\n超过阈值的跟踪脏文件 %d 个（需认领/提交/清理）：" % len(over))
            for r in over:
                print("  - %s (%.1fh)" % (r["path"], r["age_hours"]))
        if known_over:
            print("\n已登记的并行 WIP %d 个（只提示）：" % len(known_over))
            for r in known_over:
                print("  - %s (%.1fh)" % (r["path"], r["age_hours"]))
        if escalated:
            print("\n长期脏文件 %d 个（>%.0fh，升级 A0 跟踪；owner 需 7 天内处置或转 KNOWN_WIP 带到期日）："
                  % (len(escalated), args.escalate_hours))
            for r in escalated:
                print("  - 🔴 %s (%.1fh) owner≈%s" % (r["path"], r["age_hours"], r["owner_hint"]))
        print("\n协议：脏文件 >2h 提醒 owner；>4h 需在工单注明；>8h 升级 A0；>168h（7天）自动升级长期 WIP。")

    if args.strict and over:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
