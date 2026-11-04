# -*- coding: utf-8 -*-
"""11-06 08:50 定时投递：W0 决策提醒 → A0 inbox。

约束：
- 08:50 前不投递；11-07 00:00 后过期，不补发 11-06 内容。
- 登录补跑由计划任务的 LogonTrigger 负责；脚本侧只做时间/去重/收口。
- 只走 internal_notify → inbox_gateway，不新增队列。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEADLINE = datetime(2026, 11, 6, 8, 50, 0)
EXPIRE = datetime(2026, 11, 7, 0, 0, 0)
DOC = "docs/ops/2026-11-06_W0决策提醒_v1.md"
ADVICE_DOC = "docs/ops/2026-11-05_W0_22doing处置建议_v1.md"
TITLE = "W0 11-06 08:50 决策：剩余 doing/review 与 11-05 遗留"
SOURCE = "w0-1106-0850"
TASK_NAME = "W0_A0_1106_0850_Deliver"
BASELINE = (22, 32, 8)


def _current_counts() -> tuple[int, int, int, bool]:
    """返回 doing/worktree/parked，以及是否与 23:41 基线一致。"""
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


def build_body(doing: int, worktrees: int, parked: int, changed: bool = False) -> str:
    return f"""11-06 08:50 W0 决策提醒（基线 2026-11-04 23:41，08:50 复核）。

基线：doing 22 / worktree 32 / parked 8（目标 8/20/8）。
08:50 复核：doing {doing} / worktree {worktrees} / parked {parked}。{'（与基线不同，以复核数为准）' if changed else ''}

11-05 已收口 19/20 个低提交量单；CATALOG-PERSIST 仍待 ARCH1+A0 批准；W1 仍双阻塞。
11-05 的 22 doing 逐单建议：{ADVICE_DOC}（commit bcf5372d1）。

请 A0 给书面结论：
D1 11-05 的 D1–D5 是否已有结论？未决请 11-06 12:00 前明确。
D2 剩余 22 doing（DONE 6 / MERGE 9 / REVIEW 3 / KEEP 4）是否按 11-05 建议执行？
D3 若 08:50 复核仍高于 8/20/8，是否批准当日批量 done/abort 口径？
D4 CATALOG-PERSIST 与 W1 是否继续另批？
D5 11-06 16:30 HZ→UI 合入窗口是否按计划？

未获书面口径前：不批量改 tasks.json，不把 doing/worktree/parked 写成达标。"""


def _already_delivered() -> str | None:
    """11-06 后按标题/来源找本次投递，避免登录补跑重复发。"""
    base = ROOT / "agent_platform" / "bridge_inbox"
    for path in base.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if data.get("source") != SOURCE:
            continue
        payload = data.get("payload") or {}
        title = payload.get("title") or data.get("title") or ""
        submitted = data.get("submitted_at") or ""
        if title == TITLE and submitted.startswith("2026-11-06"):
            return str(path)
    return None


def _disable_task() -> None:
    """投递成功/过期后禁用一次性任务。"""
    try:
        proc = subprocess.run(
            ["C:\\Windows\\System32\\schtasks.exe", "/Change", "/TN", TASK_NAME, "/DISABLE"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if proc.returncode != 0:
            print("warn: disable task rc=%s %s" % (proc.returncode, (proc.stderr or "")[-200:]), file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - 投递已成功，禁用失败只告警
        print("warn: disable task failed: %s" % exc, file=sys.stderr)


def main() -> int:
    now = datetime.now()
    if now >= EXPIRE:
        print("skip: expired after 2026-11-06 23:59 local time")
        if "--no-disable" not in sys.argv:
            _disable_task()
        return 0
    if "--force" not in sys.argv and now < DEADLINE:
        print("skip: before 2026-11-06 08:50 local time")
        return 0

    existing = _already_delivered()
    if existing:
        print("skip: already delivered %s" % existing)
        return 0

    sys.path.insert(0, str(ROOT / "agent_platform"))
    from gateway import internal_notify

    doing, worktrees, parked, changed = _current_counts()
    body = build_body(doing, worktrees, parked, changed)
    if len(body) > internal_notify.MAX_BODY:
        raise RuntimeError("body too long: %d" % len(body))
    env = internal_notify.emit(
        "A0", TITLE, body, kind="question", source=SOURCE,
        link=DOC, actor="lead",
    )
    print(json.dumps(env, ensure_ascii=False))
    ok = bool(env.get("ok"))
    if ok and "--no-disable" not in sys.argv:
        _disable_task()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
