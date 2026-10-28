# -*- coding: utf-8 -*-
"""夜跑测试基线：分套件跑 pytest，保存基线 JSON；--check 对比上一次，新增失败即 rc=1。

用法：
  python -X utf8 tools/night_test_baseline.py --dry-run            # 只打印将执行的命令
  python -X utf8 tools/night_test_baseline.py                     # 跑默认套件并保存基线
  python -X utf8 tools/night_test_baseline.py --check             # 对比最近两次基线
  python -X utf8 tools/night_test_baseline.py --suites portal k_stock/tests --timeout 120
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "agent_platform", "data", "dev", "test_baseline")
DEFAULT_SUITES = ("portal", "k_stock/tests", "tests/e2e")

SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")
FAILED_RE = re.compile(r"^FAILED\s+(\S+)")


def parse_pytest_summary(output):
    """从 pytest -q 输出解析 passed/failed/errors/skipped 与失败用例 id 列表。"""
    result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
              "xfailed": 0, "failed_tests": []}
    for line in (output or "").splitlines():
        line = line.rstrip()
        m = FAILED_RE.match(line)
        if m:
            result["failed_tests"].append(m.group(1))
        if line.startswith("=") and ("passed" in line or "failed" in line or "error" in line):
            for num, kind in SUMMARY_RE.findall(line):
                n = int(num)
                if kind == "passed":
                    result["passed"] = n
                elif kind == "failed":
                    result["failed"] = n
                elif kind in ("error", "errors"):
                    result["errors"] = n
                elif kind == "skipped":
                    result["skipped"] = n
                elif kind == "xfailed":
                    result["xfailed"] = n
        elif "passed" in line or "failed" in line or "error" in line:
            for num, kind in SUMMARY_RE.findall(line):
                n = int(num)
                if kind == "passed":
                    result["passed"] = n
                elif kind == "failed":
                    result["failed"] = n
                elif kind in ("error", "errors"):
                    result["errors"] = n
                elif kind == "skipped":
                    result["skipped"] = n
                elif kind == "xfailed":
                    result["xfailed"] = n
    return result


def diff_failures(prev, cur):
    """返回新增失败用例（cur - prev），用于 --check。"""
    prev_tests = set()
    for suite in (prev or {}).get("suites", []):
        prev_tests.update(suite.get("failed_tests") or [])
    cur_tests = set()
    for suite in (cur or {}).get("suites", []):
        cur_tests.update(suite.get("failed_tests") or [])
    return sorted(cur_tests - prev_tests)


def run_suite(suite, timeout, dry_run=False):
    cmd = [sys.executable, "-m", "pytest", suite, "-q",
           "--timeout=%s" % timeout, "-p", "no:cacheprovider"]
    print("[night] " + " ".join(cmd), flush=True)
    if dry_run:
        return {"suite": suite, "command": " ".join(cmd), "dry_run": True,
                "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
                "xfailed": 0, "failed_tests": [], "rc": 0}
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary = parse_pytest_summary(out)
    summary.update({"suite": suite, "command": " ".join(cmd), "rc": proc.returncode})
    return summary


def load_latest_previous(out_dir):
    files = sorted([f for f in os.listdir(out_dir) if f.endswith(".json")]) if os.path.isdir(out_dir) else []
    if len(files) < 2:
        return None
    with io.open(os.path.join(out_dir, files[-2]), "r", encoding="utf-8") as f:
        return json.load(f)


TOOLS = os.path.dirname(os.path.abspath(__file__))


def build_dep_age_command(limit=0):
    cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "dep_age_check.py"), "--json"]
    if limit and limit > 0:
        cmd += ["--limit", str(limit)]
    return cmd


def summarize_dep_age(payload):
    fresh = payload.get("fresh") or []
    return {
        "checked": payload.get("checked", 0),
        "fresh_count": payload.get("fresh_count", len(fresh)),
        "error_count": payload.get("error_count", 0),
        "cutoff": payload.get("cutoff", ""),
        "fresh_top": [
            {"name": x.get("name"), "version": x.get("version"), "age_days": x.get("age_days")}
            for x in fresh[:10]
        ],
        "warn_only": True,
    }


def run_dep_age(limit=0, timeout_s=600):
    """跑依赖年龄检查；任何失败只记录，不影响夜跑 rc（warn-only）。"""
    try:
        proc = subprocess.run(
            build_dep_age_command(limit), cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s,
        )
        payload = json.loads((proc.stdout or "").strip())
        out = summarize_dep_age(payload)
        out.update({"ok": True, "rc": proc.returncode})
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="夜跑测试基线")
    ap.add_argument("--suites", nargs="+", default=list(DEFAULT_SUITES))
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="对比最近两次基线，新增失败 rc=1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-dep-age", action="store_true", help="跳过依赖年龄检查段")
    ap.add_argument("--dep-age-limit", type=int, default=0, help="只查前 N 个包（0=全部）")
    ap.add_argument("--dep-age-timeout", type=int, default=600)
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)

    if args.check:
        files = sorted([f for f in os.listdir(args.out_dir) if f.endswith(".json")])
        if len(files) < 2:
            print("[night] 基线不足 2 份，无法对比（至少跑两次）")
            return 0
        with io.open(os.path.join(args.out_dir, files[-2]), "r", encoding="utf-8") as f:
            prev = json.load(f)
        with io.open(os.path.join(args.out_dir, files[-1]), "r", encoding="utf-8") as f:
            cur = json.load(f)
        new = diff_failures(prev, cur)
        print("[night] prev=%s cur=%s new_failures=%d" % (files[-2], files[-1], len(new)))
        for t in new:
            print("  NEW FAILURE " + t)
        return 0 if not new else 1

    suites = [run_suite(s, args.timeout, args.dry_run) for s in args.suites]
    if args.skip_dep_age:
        dep_age = {"skipped": True}
    elif args.dry_run:
        dep_age = {"dry_run": True, "command": " ".join(build_dep_age_command(args.dep_age_limit))}
    else:
        dep_age = run_dep_age(args.dep_age_limit, args.dep_age_timeout)
        if dep_age.get("ok"):
            print("[night] dep_age checked=%d fresh=%d errors=%d cutoff=%s (warn-only)" % (
                dep_age.get("checked", 0), dep_age.get("fresh_count", 0),
                dep_age.get("error_count", 0), dep_age.get("cutoff", "")))
            for x in dep_age.get("fresh_top") or []:
                print("  FRESH %s==%s age=%sd" % (x.get("name"), x.get("version"), x.get("age_days")))
        else:
            print("[night] dep_age skip: %s（warn-only，不影响 rc）" % dep_age.get("error"))
    stamp = datetime.now().strftime("%Y-%m-%d")
    doc = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": stamp,
        "suites": suites,
        "dep_age": dep_age,
        "totals": {
            "passed": sum(s.get("passed", 0) for s in suites),
            "failed": sum(s.get("failed", 0) for s in suites),
            "errors": sum(s.get("errors", 0) for s in suites),
            "failed_tests": [t for s in suites for t in (s.get("failed_tests") or [])],
        },
    }
    if args.dry_run:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0
    path = os.path.join(args.out_dir, stamp + ".json")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    new = diff_failures(load_latest_previous(args.out_dir), doc)
    print("[night] saved %s passed=%d failed=%d errors=%d new_failures=%d" % (
        path, doc["totals"]["passed"], doc["totals"]["failed"],
        doc["totals"]["errors"], len(new)))
    return 0 if doc["totals"]["failed"] == 0 and doc["totals"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
