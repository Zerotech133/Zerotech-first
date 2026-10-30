#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P2 周报包装器（只读）。

每周一由 ZeroBound-P2-Weekly 计划任务调用：
1. a0_patrol.py          成员活跃/静默/超期/督办
2. redteam_daily.py      红队周报（--dry，不落判定副作用）
3. zhuge_planner.py      基于前两者的整改计划模板

只写 work/p2/weekly/<YYYYMMDD>/ 下的报告，不写业务数据、不派单、不执行整改。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "work" / "p2" / "weekly"
PY = sys.executable


def _run(script: str, args: list[str], timeout: int = 300) -> dict:
    cmd = [PY, str(ROOT / "tools" / script), *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, timeout=timeout, text=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"script": script, "rc": 124, "stdout": "", "stderr": "timeout"}
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    return {"script": script, "rc": proc.returncode, "stdout": out, "stderr": err}


def _parse(text: str):
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return None
        return None


def _digest(patrol: dict, red: dict, planner: dict) -> dict:
    p = patrol or {}
    r = red or {}
    pl = planner or {}
    return {
        "patrol": {
            "members": p.get("n_members"),
            "active_48h": p.get("n_active_48h"),
            "idle_48h": p.get("n_idle"),
            "overdue_doing": len(p.get("overdue_doing") or []),
            "review_stuck": len(p.get("review_stuck") or []),
            "escalations": len(p.get("escalations") or []),
            "verdict": p.get("verdict"),
        },
        "redteam": {
            "runs": r.get("runs"),
            "cases": r.get("total_cases"),
            "passed": r.get("passed"),
            "failed": r.get("failed"),
            "pass_rate": r.get("pass_rate"),
            "report": r.get("report"),
        },
        "planner": {
            "steps": len(pl.get("steps") or []),
            "read_only": pl.get("read_only"),
            "executable": pl.get("executable"),
            "risks": pl.get("risks") or [],
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2 只读周报")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args(argv)

    day = dt.date.today()
    today = day.strftime("%Y%m%d")
    outdir = OUT_ROOT / today
    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)

    patrol_raw = _run("a0_patrol.py", ["--json"])
    red_raw = _run("redteam_daily.py", ["--weekly", "--dry", "--json"])
    question = "P2 周报：基于本次 patrol 与 red-team 结果，给出本周整改计划与验收判据"
    planner_raw = _run("zhuge_planner.py", [question, "--json"])

    patrol = _parse(patrol_raw["stdout"])
    red = _parse(red_raw["stdout"])
    planner = _parse(planner_raw["stdout"])
    digest = _digest(patrol, red, planner)
    ok = all(x["rc"] == 0 for x in (patrol_raw, red_raw, planner_raw))
    ok = ok and bool(red and red.get("ok", True))

    if not args.dry_run:
        (outdir / "patrol.json").write_text(
            json.dumps(patrol if patrol is not None else {"raw": patrol_raw["stdout"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (outdir / "redteam.json").write_text(
            json.dumps(red if red is not None else {"raw": red_raw["stdout"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (outdir / "planner.json").write_text(
            json.dumps(planner if planner is not None else {"raw": planner_raw["stdout"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (outdir / "summary.json").write_text(
            json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                        "ok": ok, "digest": digest}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        md = [
            f"# P2 周报 {day.isoformat()}",
            "",
            f"- 状态：{'PASS' if ok else 'PARTIAL'}",
            f"- patrol：成员 {digest['patrol']['members']} / 活跃 {digest['patrol']['active_48h']} / "
            f"静默 {digest['patrol']['idle_48h']} / 超期 {digest['patrol']['overdue_doing']} / "
            f"督办 {digest['patrol']['escalations']}",
            f"- red-team：{digest['redteam']['passed']}/{digest['redteam']['cases']} passed，"
            f"通过率 {digest['redteam']['pass_rate']}",
            f"- planner：{digest['planner']['steps']} 步；"
            f"read_only={digest['planner']['read_only']} executable={digest['planner']['executable']}",
            f"- patrol verdict：{digest['patrol']['verdict']}",
            "",
            "## 说明",
            "",
            "只读周报；不派单、不执行整改。planner 为规则模板 v0。",
        ]
        (outdir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps({"ok": ok, "outdir": str(outdir), "digest": digest},
                     ensure_ascii=False, indent=2))
    if os_err := (patrol_raw["stderr"] + red_raw["stderr"] + planner_raw["stderr"]).strip():
        print("stderr: " + os_err[:2000], file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
