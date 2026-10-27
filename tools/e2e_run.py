# -*- coding: utf-8 -*-
"""E2E 运行器（O5）：跑 tests/e2e 首批冒烟场景，产 artifact。

用法：
  python -X utf8 tools/e2e_run.py            # 跑全部首批场景
  python -X utf8 tools/e2e_run.py --list     # 列出场景目录
  python -X utf8 tools/e2e_run.py --suite smoke_services
  python -X utf8 tools/e2e_run.py --no-artifact
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E2E_DIR = os.path.join(REPO, "tests", "e2e")
OUT_DIR = os.path.join(REPO, "agent_platform", "data", "dev", "e2e")
SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")

# 场景目录（与 docs/specs/2026-10-27_E2E场景集_v1.md 对齐）
SCENARIOS = [
    {"id": "E2E-01", "name": "门户入口 8734", "status": "implemented", "path": "test_smoke_services.py"},
    {"id": "E2E-02", "name": "门户 API 8091 health", "status": "implemented", "path": "test_smoke_services.py"},
    {"id": "E2E-03", "name": "军团 8796 MCP servers", "status": "implemented", "path": "test_smoke_services.py"},
    {"id": "E2E-04", "name": "C6 日报可读", "status": "implemented", "path": "test_smoke_tools.py"},
    {"id": "E2E-05", "name": "质量分级 none=0", "status": "implemented", "path": "test_smoke_tools.py"},
    {"id": "E2E-06", "name": "扩展 lint/对账一致", "status": "implemented", "path": "test_smoke_tools.py"},
    {"id": "E2E-07", "name": "MCP 安全自查 0 error", "status": "implemented", "path": "test_smoke_tools.py"},
    {"id": "E2E-08", "name": "飞书 bot 存活/心跳", "status": "implemented", "path": "test_smoke_bot.py"},
    {"id": "E2E-09", "name": "飞书私聊→命令→回执（mock）", "status": "implemented", "path": "test_smoke_bot.py"},
    {"id": "E2E-10", "name": "失败路径（8091 离线/8796 401）", "status": "planned", "path": ""},
    {"id": "E2E-11", "name": "L2 重启后健康", "status": "planned", "path": ""},
]


def parse_summary(text):
    out = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}
    for line in (text or "").splitlines():
        if line.startswith("FAILED "):
            out["failed_tests"].append(line[7:].strip())
        if ("passed" in line or "failed" in line or "error" in line) and "==" not in line:
            for num, kind in SUMMARY_RE.findall(line):
                n = int(num)
                if kind == "passed":
                    out["passed"] = n
                elif kind == "failed":
                    out["failed"] = n
                elif kind in ("error", "errors"):
                    out["errors"] = n
                elif kind == "skipped":
                    out["skipped"] = n
    return out


def list_scenarios():
    for s in SCENARIOS:
        print("%-7s %-10s %s" % (s["id"], s["status"], s["name"]))


def run_tests(suite=None):
    args = [sys.executable, "-m", "pytest", "tests/e2e", "-q",
            "--timeout=120", "-p", "no:cacheprovider"]
    if suite:
        args = [sys.executable, "-m", "pytest", os.path.join("tests", "e2e", suite),
                "-q", "--timeout=120", "-p", "no:cacheprovider"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=600)
    dur = round(time.time() - t0, 1)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    summary = parse_summary(out)
    return p.returncode, dur, out, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="E2E 运行器（首批冒烟）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--suite", default=None, help="测试文件名（如 test_smoke_services.py）")
    ap.add_argument("--no-artifact", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.list:
        list_scenarios()
        return 0
    if not os.path.isdir(E2E_DIR):
        print("tests/e2e 不存在", file=sys.stderr)
        return 2
    rc, dur, out, summary = run_tests(args.suite)
    if args.json:
        print(json.dumps({"rc": rc, "duration_sec": dur, "summary": summary}, ensure_ascii=False, indent=2))
    else:
        print("e2e: rc=%s passed=%d failed=%d errors=%d skipped=%d duration=%ss" % (
            rc, summary["passed"], summary["failed"], summary["errors"],
            summary["skipped"], dur))
        for t in summary["failed_tests"]:
            print("  FAILED " + t)
    if not args.no_artifact:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = os.path.join(OUT_DIR, stamp + ".json")
        doc = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "suite": args.suite or "all",
            "rc": rc,
            "duration_sec": dur,
            "summary": summary,
            "scenarios": SCENARIOS,
        }
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print("artifact: " + path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
