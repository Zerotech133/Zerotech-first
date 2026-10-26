# -*- coding: utf-8 -*-
"""eval-harness：10 条金标任务 + 断言评分 + 回归报告。

用法：
  python -X utf8 tools/eval_harness.py list [--json]
  python -X utf8 tools/eval_harness.py run [--id ID] [--json] [--out path]
结果落 `work/eval/result-<ts>.json`。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GOLDEN = REPO / "eval" / "golden_tasks.json"
OUTDIR = REPO / "work" / "eval"


def load_tasks(path: Path | None = None) -> list:
    p = Path(path) if path else GOLDEN
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("tasks") or []


def _subst(cmd: list) -> list:
    return [str(x).replace("{python}", sys.executable).replace("{repo}", str(REPO)) for x in cmd]


def run_task(t: dict, cwd: Path | None = None, timeout: int = 0) -> dict:
    cmd = _subst(t.get("cmd") or [])
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=timeout or int(t.get("timeout") or 120))
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -9, "", "TIMEOUT"
    ok = rc == int(t.get("expect_rc", 0))
    if ok and t.get("expect_contains"):
        ok = str(t["expect_contains"]) in (out + err)
    return {"id": t.get("id"), "desc": t.get("desc"), "rc": rc, "ok": ok,
            "secs": round(time.time() - t0, 2),
            "stdout_tail": out[-1200:], "stderr_tail": err[-800:]}


def run_all(tasks_path: Path | None = None, only: str = "") -> dict:
    tasks = load_tasks(tasks_path)
    results = [run_task(t) for t in tasks if not only or t.get("id") == only]
    passed = sum(1 for r in results if r["ok"])
    out = {"schema": "eval_result/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "total": len(results), "passed": passed, "failed": len(results) - passed,
           "score": round(passed / len(results), 3) if results else 0.0,
           "results": results}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    f = OUTDIR / ("result-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["result_path"] = str(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 eval-harness（金标任务回归）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--id", default="")
    r.add_argument("--json", action="store_true")
    r.add_argument("--out", default="")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        tasks = load_tasks()
        print(json.dumps(tasks, ensure_ascii=False, indent=1) if args.json else
              "\n".join("%-22s %s" % (t.get("id"), t.get("desc")) for t in tasks))
        return 0
    res = run_all(only=args.id)
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("passed=%d/%d score=%.3f" % (res["passed"], res["total"], res["score"]))
        for x in res["results"]:
            print("%s %-22s rc=%s %.2fs" % ("PASS" if x["ok"] else "FAIL", x["id"], x["rc"], x["secs"]))
        print("result", res["result_path"])
    return 0 if res["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
