# -*- coding: utf-8 -*-
"""Phase D1：按任务校验 evidence artifact（测试结果 + 证据）。

用法：python tools/check_task_artifact.py --task WO-X [--dir <evidence>]
退出码 0=通过，1=缺失/测试失败。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DEFAULT_DIR = r"C:\项目\_vault\legion_ide\evidence"


def latest_for_task(task: str, directory: str | None = None) -> dict | None:
    root = Path(directory or os.environ.get("LEGION_EVIDENCE_DIR") or DEFAULT_DIR)
    if not root.is_dir():
        return None
    best = None
    for p in root.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(rec.get("task") or "") != str(task):
            continue
        ts = str(rec.get("finalized_at") or rec.get("created_at") or "")
        if best is None or ts > str(best.get("finalized_at") or best.get("created_at") or ""):
            best = rec
    return best


def _test_result(rec: dict) -> dict:
    for ev in rec.get("events") or []:
        if ev.get("event") == "test_result":
            return ev.get("payload") or {}
    return {}


def check(task: str, directory: str | None = None) -> dict:
    rec = latest_for_task(task, directory)
    if not rec:
        return {"ok": False, "error": "evidence_missing", "task": task}
    tr = _test_result(rec)
    ok = bool(rec.get("ok", tr.get("ok")))
    out = {
        "ok": ok,
        "task": task,
        "run_id": rec.get("run_id"),
        "evidence": rec.get("run_id"),
        "test": {"ok": tr.get("ok"), "exit_code": tr.get("exit_code"),
                 "output_sha256": tr.get("output_sha256")},
        "has_signature": bool(rec.get("signature")),
    }
    if not ok:
        out["error"] = "test_failed_or_missing"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验任务 evidence artifact")
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default="")
    args = ap.parse_args(argv)
    out = check(args.task, args.dir or None)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
