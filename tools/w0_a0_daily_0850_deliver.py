# -*- coding: utf-8 -*-
"""每日 08:50 W0 决策提醒：无日期专用脚本时的通用投递。

由 W0_A0_0850_Deliver 的每日触发器调用；有日期专用脚本时由 wrapper 优先调用
tools/w0_a0_MMdd_0850_deliver.py，本脚本只做兜底。

约束：
- 周末不发；08:50 前不发；同一天已发不重发。
- 不自动禁用每日任务；只走 internal_notify -> inbox_gateway。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/ops/2026-11-05_W0_22doing处置建议_v1.md"
BASELINE = (22, 32, 8)
START_DATE = date(2026, 11, 6)


def _current_counts() -> tuple[int, int, int, bool]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "w0_inventory.py"), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("w0_inventory rc=%s stderr=%s" % (proc.returncode, proc.stderr[-400:]))
    inv = json.loads(proc.stdout)
    doing = int(inv["doing"])
    worktrees = int(inv["worktrees"])
    tasks_file = ROOT / ".worktrees" / "tasks.json"
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    parked = sum(1 for t in tasks.values() if isinstance(t, dict) and t.get("status") == "parked")
    return doing, worktrees, parked, (doing, worktrees, parked) != BASELINE


def build_body(day: date, doing: int, worktrees: int, parked: int, changed: bool = False) -> str:
    return f"""{day.isoformat()} 每日 08:50 W0 决策提醒（基线 doing 22 / worktree 32 / parked 8，目标 8/20/8）。

08:50 复核：doing {doing} / worktree {worktrees} / parked {parked}。{'（与基线不同，以复核数为准）' if changed else ''}

逐单依据：{DOC}。
请 A0 给书面结论：
D1 6 个 DONE 单是否批准立即 done（分支保留、worktree 回收）？
D2 9 个 K-STOCK 合入单的排序与 HZ/UI 优先是否批准？
D3 MON/DATA2 脏 worktree 由谁提交/审查？
D4 UI 三支（MOBILE-RESIDUAL/GATECHECK/A11Y2）验收人/门禁口径？
D5 CATALOG-PERSIST/W1 是否继续另批？

未获书面口径前：不批量改 tasks.json，不把 doing/worktree/parked 写成达标。"""


def _already_delivered(day: date, source: str) -> str | None:
    base = ROOT / "agent_platform" / "bridge_inbox"
    for path in base.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if data.get("source") != source:
            continue
        submitted = data.get("submitted_at") or ""
        if submitted.startswith(day.isoformat()):
            return str(path)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; only for dry-run/testing")
    parser.add_argument("--dry-run", action="store_true", help="build and print plan; do not emit")
    args = parser.parse_args(argv)

    now = datetime.now()
    day = date.fromisoformat(args.date) if args.date else now.date()
    source = "w0-%s-0850" % day.strftime("%m%d")
    title = "W0 %s 08:50 每日决策：doing/worktree/parked 与 D1–D5" % day.isoformat()
    deadline = datetime.combine(day, time(8, 50))

    if day < START_DATE:
        print("skip: before daily start date %s" % START_DATE.isoformat())
        return 0
    if day.weekday() >= 5:
        print("skip: weekend %s" % day.isoformat())
        return 0
    if not args.dry_run and now < deadline:
        print("skip: before %s 08:50 local time" % day.isoformat())
        return 0

    existing = _already_delivered(day, source)
    if existing and not args.dry_run:
        print("skip: already delivered %s" % existing)
        return 0

    doing, worktrees, parked, changed = _current_counts()
    body = build_body(day, doing, worktrees, parked, changed)
    if len(body) > 4000:
        raise RuntimeError("body too long: %d" % len(body))
    if args.dry_run:
        print(json.dumps({"dry_run": True, "date": day.isoformat(), "source": source,
                          "title": title, "body_len": len(body)}, ensure_ascii=False))
        return 0

    sys.path.insert(0, str(ROOT / "agent_platform"))
    from gateway import internal_notify

    env = internal_notify.emit(
        "A0", title, body, kind="question", source=source,
        link=DOC, actor="lead",
    )
    print(json.dumps(env, ensure_ascii=False))
    return 0 if env.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
