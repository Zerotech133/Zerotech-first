#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W0 merge 预处理（不删、不合并）：
- 给 doing 单的 worktree 补齐主仓未跟踪数据文件（catalog/registry/xueqiu db），
  否则 test_gate 的 datawarehouse 套件在 worktree 内必红；
- 把主仓 test_gate.ps1 / hq_meeting_record.py 基线同步到分支（仅当分支未改这两个文件），
  解决 H5 钉扎/语法门在旧基线分支上的全局失败。
- 只做预处理；merge 仍走 devtask merge + 质量闸。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WT_DIR = ROOT / ".worktrees"
TASKS = WT_DIR / "tasks.json"
DATA_FILES = [
    "quant_backtest/data/catalog.json",
    "quant_backtest/data/warehouse_registry.json",
    "quant_backtest/data/xueqiu/xueqiu_info.db",
]
BASE_FILES = ["tools/test_gate.ps1", "tools/hq_meeting_record.py"]


def _run(args, cwd=None):
    p = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)  # 同卷硬链接，读测试足够；不复制 30MB
    except OSError:
        shutil.copy2(src, dst)


def main() -> int:
    tasks = json.loads(TASKS.read_text(encoding="utf-8-sig"))
    rows = []
    for tid, t in tasks.items():
        if not isinstance(t, dict) or t.get("status") != "doing":
            continue
        wt = Path(t.get("worktree") or "")
        if not wt.is_dir():
            rows.append({"task": tid, "status": "missing_worktree"})
            continue
        added = []
        for rel in DATA_FILES:
            src = ROOT / rel
            if src.is_file():
                _link_or_copy(src, wt / rel)
                added.append(rel)
        # 只有分支未改这两文件才同步主仓基线
        rc, out = _run(["git", "-C", str(wt), "diff", "--name-only", "master...HEAD"])
        changed = {x.strip() for x in out.splitlines() if x.strip()} if rc == 0 else set()
        base_changed = []
        for rel in BASE_FILES:
            if rel in changed:
                continue
            src = ROOT / rel
            dst = wt / rel
            if src.is_file() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                base_changed.append(rel)
        if base_changed:
            _run(["git", "-C", str(wt), "add", "--"] + base_changed)
            rc, out = _run(["git", "-C", str(wt), "-c", "user.name=DSH-Lead",
                            "-c", "user.email=dsh@local", "commit", "-m",
                            "W0 prep: align gate/syntax baseline"] + ["--"] + base_changed)
            commit = "ok" if rc == 0 else "commit_fail"
        else:
            commit = "noop"
        rows.append({"task": tid, "status": "prepared", "data": len(added), "base_commit": commit})
    out = ROOT / "deliverables" / "w0_cleanup" / "2026-11-03" / "prep_report.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prepared": len([x for x in rows if x["status"] == "prepared"]),
                      "report": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
