# -*- coding: utf-8 -*-
"""Merge 窗口批处理（2026-11-16 WO-MERGE-WINDOW）。

`devtask.py merge` 只允许在 merge train 窗口内执行（16:30–17:00 / 21:40–22:10）。
本脚本把「到点合哪几条」做成可调度的一次性动作：

1. **窗口自检**：不在窗口内直接拒绝（rc=2），防止被误触发在窗口外改 master；
2. 逐条 `devtask.py merge <TID>`，一条失败不影响下一条（各自 rc 记录）；
3. 结果落 `deliverables/window_merge/<date>_<hhmm>.json` + `logs/merge_window.log`。

用法::

    python -X utf8 tools/merge_window_run.py --tids A,B --dry-run
    python -X utf8 tools/merge_window_run.py --tids A,B            # 仅窗口内可跑
    python -X utf8 tools/merge_window_run.py --tids A --force      # 紧急放行（留痕）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO.parent / "deliverables" / "window_merge"
LOG = REPO / "logs" / "merge_window.log"
WINDOWS = (("16:30", "17:00"), ("21:40", "22:10"))


def log(msg: str) -> None:
    line = "%s %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _now() -> dt.datetime:
    """当前时间（测试可 monkeypatch，避免改全局 datetime）。"""
    return dt.datetime.now()


def _parse_now(text: str) -> dt.datetime:
    """解析 `--now`：支持 `HH:MM`（今天）与完整 ISO。仅用于 dry-run 演练。"""
    text = str(text).strip()
    if re.match(r"^\d{2}:\d{2}$", text):
        hm = dt.datetime.strptime(text, "%H:%M")
        base = dt.datetime.now()
        return base.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
    return dt.datetime.fromisoformat(text)


def in_window(now: dt.datetime) -> tuple[bool, str]:
    hm = now.strftime("%H:%M")
    for start, end in WINDOWS:
        if start <= hm <= end:
            return True, "%s-%s" % (start, end)
    return False, "当前 %s 不在 %s 内" % (hm, " / ".join("%s-%s" % w for w in WINDOWS))


def rebase_branch(tid: str, timeout: int = 600) -> dict:
    """把 worktree 里的分支 rebase 到当前 master（窗口前漂移修正）。

    失败立即 `--abort`，不留半路状态；这条失败不影响其余条目。
    """
    wt = REPO / ".worktrees" / tid
    if not wt.is_dir():
        return {"ok": False, "tail": "worktree_missing:%s" % wt}
    try:
        r = subprocess.run(["git", "-C", str(wt), "rebase", "master"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8",
                           errors="replace", timeout=timeout)
        if r.returncode != 0:
            subprocess.run(["git", "-C", str(wt), "rebase", "--abort"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=120)
            return {"ok": False, "tail": "rebase_conflict（已 abort，未改 master）"}
        return {"ok": True, "tail": "rebased"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tail": "rebase_error:%s" % type(exc).__name__}


def run_merge(tid: str, timeout: int) -> dict:
    t0 = time.perf_counter()
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", "tools/devtask.py", "merge", tid],
                           cwd=str(REPO), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        tail = out[-1][:200] if out else ""
        return {"tid": tid, "rc": r.returncode, "seconds": round(time.perf_counter() - t0, 1),
                "tail": tail, "ok": r.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"tid": tid, "rc": 124, "seconds": round(time.perf_counter() - t0, 1),
                "tail": "timeout>%ss" % timeout, "ok": False}
    except Exception as exc:  # noqa: BLE001
        return {"tid": tid, "rc": 1, "seconds": round(time.perf_counter() - t0, 1),
                "tail": type(exc).__name__, "ok": False}


STEP_REBASE = "rebase"
STEP_MERGE = "merge"


def plan_for(tid: str, no_rebase: bool = False) -> list[dict]:
    """单条任务的执行计划。

    **dry-run 与实际执行共用本函数**——这是「演练顺序 = 执行顺序」的唯一保证。
    新增任何前置步骤只改这里，两边同时生效，不会各写一套漂移。
    """
    steps: list[dict] = []
    if not no_rebase:
        steps.append({"kind": STEP_REBASE,
                      "cmd": "git -C .worktrees/%s rebase master" % tid})
    steps.append({"kind": STEP_MERGE,
                  "cmd": "python tools/devtask.py merge %s" % tid})
    return steps


def execute_step(step: dict, tid: str, timeout: int) -> dict:
    """执行计划中的一步，返回统一结果结构（kind/ok/tail，merge 另带 rc/seconds）。"""
    if step["kind"] == STEP_REBASE:
        r = rebase_branch(tid)
        return {"kind": STEP_REBASE, "ok": bool(r.get("ok")), "tail": r.get("tail", "")}
    r = run_merge(tid, timeout)
    return {"kind": STEP_MERGE, "ok": bool(r.get("ok")), "rc": r.get("rc"),
            "seconds": r.get("seconds"), "tail": r.get("tail", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Merge 窗口批处理（窗口外拒绝）")
    ap.add_argument("--tids", required=True, help="逗号分隔的任务 TID")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="窗口外紧急放行（留痕，需 24h 复盘）")
    ap.add_argument("--timeout", type=int, default=1800, help="单条 merge 超时秒")
    ap.add_argument("--no-rebase", action="store_true",
                    help="跳过窗口前的 rebase（默认先 rebase master 修正漂移）")
    ap.add_argument("--now", default=None,
                    help="模拟当前时间（HH:MM 或 ISO）；**仅允许与 --dry-run 同用**，防绕过窗口守卫")
    args = ap.parse_args(argv)

    tids = [t.strip() for t in args.tids.split(",") if t.strip()]
    if args.now:
        if not args.dry_run:
            log("REFUSED --now 只能与 --dry-run 同用（防止伪造时间绕过窗口守卫）")
            return 2
        now = _parse_now(args.now)
    else:
        now = _now()
    ok_window, why = in_window(now)
    log("START tids=%s window=%s dry_run=%s force=%s" % (tids, why, args.dry_run, args.force))

    if args.dry_run:
        planned = []
        for tid in tids:
            plan = plan_for(tid, args.no_rebase)
            planned.append({"tid": tid, "steps": plan,
                            "kinds": [s["kind"] for s in plan]})
            log("  [dry] %s" % " → ".join(s["cmd"] for s in plan))
        payload = {"schema": "merge_window_run/v1", "dry_run": True,
                   "at": now.astimezone().isoformat(timespec="seconds"),
                   "window": why, "window_ok": ok_window, "planned": planned, "ok": True}
        out = OUT_DIR / now.strftime("%Y%m%d") / ("plan_%s.json" % now.strftime("%H%M%S"))
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            log("WROTE(plan) %s" % out)
        except OSError as exc:
            log("WARN 计划未落盘：%s" % type(exc).__name__)
        log("SUMMARY dry_run planned=%d window_ok=%s" % (len(tids), ok_window))
        return 0
    if not ok_window and not args.force:
        log("REFUSED 不在 merge 窗口内（%s）；用 --force 才可紧急放行" % why)
        return 2
    if args.force and not ok_window:
        log("WARN --force 窗口外放行：%s（需 24h 复盘）" % why)

    results = []
    for tid in tids:
        plan = plan_for(tid, args.no_rebase)  # 与 dry-run 同源
        steps: list[dict] = []
        for step in plan:
            got = execute_step(step, tid, args.timeout)
            steps.append(got)
            log("  %s %s ok=%s %s" % (tid, got["kind"], got["ok"], got.get("tail", "")))
            if not got["ok"]:
                break  # 前置失败即停：不继续该条后续步骤（绝不半路硬来）
        ok = len(steps) == len(plan) and all(s["ok"] for s in steps)
        results.append({"tid": tid, "plan": [s["kind"] for s in plan],
                        "steps": steps, "ok": ok})
    for r in results:
        log("  RESULT %s ok=%s steps=%d/%d" % (r["tid"], r["ok"], len(r["steps"]), len(r["plan"])))
    payload = {"schema": "merge_window_run/v2",
               "at": now.astimezone().isoformat(timespec="seconds"),
               "window": why, "forced": bool(args.force and not ok_window),
               "results": results, "ok": all(r["ok"] for r in results)}
    out = OUT_DIR / now.strftime("%Y%m%d") / ("merge_%s.json" % now.strftime("%H%M%S"))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log("WROTE %s" % out)
    except OSError as exc:
        log("WARN 结果未落盘：%s" % type(exc).__name__)
    log("SUMMARY merged=%d/%d" % (sum(1 for r in results if r["ok"]), len(results)))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
