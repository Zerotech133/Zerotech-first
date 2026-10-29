#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flaky 复验探针（2026-10-29，关羽）。

用途：每周（或重大改动后）跑 N 轮真实测试套件，统计失败项并与基线 flaky 清单比对，输出：
  - 实际 FAILED 集合 vs 基线 flaky（命中=已知；未命中=新 flaky/回归）
  - 每项复现次数/总轮数
用法：
  python tools/flaky_probe.py --suite agent --runs 2
  python tools/flaky_probe.py --suite portal --runs 3
退出码：0=无新失败（所有失败均属基线 known/flaky）；1=出现未登记失败。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "tools" / "test_baseline.json"
SUITE_DIRS = {
    "agent": "agent_platform/tests",
    "portal": "portal/tests",
    "kstock": "k_stock/tests",
}
FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.M)


def _load_baseline():
    b = json.loads(BASELINE.read_text(encoding="utf-8"))
    known = {}
    for suite, v in b.items():
        if not isinstance(v, dict):
            continue
        acc = known.setdefault(suite, {"known": [], "flaky": [], "env": []})
        for k in ("known_failures", "flaky", "env_failures"):
            acc[k.replace("_failures", "").replace("_failures", "")] = list(v.get(k) or [])
    return b, known


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="flaky 复验探针")
    ap.add_argument("--suite", choices=sorted(SUITE_DIRS), default="agent")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    suite_dir = SUITE_DIRS[args.suite]
    b, known = _load_baseline()
    key = suite_dir
    base = known.get(key, {"known": [], "flaky": [], "env": []})
    base_flaky = set(base.get("flaky") or [])
    base_known = set(base.get("known") or []) | set(base.get("env") or [])

    counts = {}
    for i in range(1, args.runs + 1):
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "pytest", suite_dir, "-q", "--timeout=180",
                            "-p", "no:cacheprovider"],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=args.timeout)
        fails = FAILED_RE.findall(p.stdout or "")
        for f in fails:
            counts[f] = counts.get(f, 0) + 1
        print("[run %d/%d] rc=%d failed=%d 用时%.0fs"
              % (i, args.runs, p.returncode, len(fails), time.time() - t0))
        for f in fails:
            print("    FAILED", f)

    new_fails = [f for f in counts if f not in base_flaky and f not in base_known]
    flaky_hits = {f: c for f, c in counts.items() if f in base_flaky}
    known_hits = {f: c for f, c in counts.items() if f in base_known}

    print("\n== 汇总（suite=%s runs=%d）==" % (args.suite, args.runs))
    print("基线 flaky 命中：%s" % (json.dumps(flaky_hits, ensure_ascii=False) if flaky_hits else "无"))
    print("基线 known/env 命中：%s" % (json.dumps(known_hits, ensure_ascii=False) if known_hits else "无"))
    print("未登记失败：%s" % (json.dumps(new_fails, ensure_ascii=False) if new_fails else "无"))

    out = {"suite": args.suite, "runs": args.runs, "counts": counts,
           "flaky_hits": flaky_hits, "known_hits": known_hits, "new_fails": new_fails,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    dst = REPO / "agent_platform" / "data" / "flaky_probe"
    dst.mkdir(parents=True, exist_ok=True)
    fp = dst / ("probe_%s_%s.json" % (args.suite, time.strftime("%Y%m%d_%H%M%S")))
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("结果落盘：%s" % fp)
    return 0 if not new_fails else 1


if __name__ == "__main__":
    sys.exit(main())
