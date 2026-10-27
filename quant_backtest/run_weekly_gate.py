# -*- coding: utf-8 -*-
"""PytestWeekly 门禁 runner（W9 扩全量，K-STOCK-393）。

替换原 51 用例金样子集，两级门禁：
  1) collect-only 收集门禁（~30s）：任何 test_*.py 的收集炸窗
     （如脚本式 sys.exit 炸 pytest 收集的 K-STOCK-392 同款）当场现形；
  2) 全量 pytest（全仓 1181+ 用例，~4min）。
任一级失败退出码非 0（SYSTEM 计划任务的 RestartOnFailure 会重试并留痕）。

用法：python run_weekly_gate.py [--quick]
  --quick: 只跑收集门禁（调试用）
路径经由 C:\aios junction（ASCII），适配 SYSTEM 计划任务环境。
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or r"C:\Program Files\Python311\python.exe"


def run(args):
    proc = subprocess.run(
        args,
        cwd=HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    t0 = time.time()
    all_tests = sorted(
        f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py")
    )
    if not all_tests:
        print("GATE FAIL: no test_*.py found in", HERE)
        return 2
    print("[gate1] collect-only over %d test files" % len(all_tests))
    rc, out = run(
        [PY, "-m", "pytest"]
        + all_tests
        + ["-q", "--collect-only", "-p", "no:cacheprovider"]
    )
    tail = "\n".join(out.strip().splitlines()[-3:])
    print(tail)
    if rc != 0:
        print("GATE FAIL: collection crashed (rc=%d)" % rc)
        print(out[-3000:])
        return 1
    if "--quick" in sys.argv:
        print("[gate1] PASS in %.0fs (--quick, skip full run)" % (time.time() - t0))
        return 0
    print("[gate2] full pytest run")
    rc, out = run(
        [PY, "-m", "pytest"]
        + all_tests
        + ["-q", "--tb=short", "-p", "no:cacheprovider"]
    )
    tail = "\n".join(out.strip().splitlines()[-25:])
    print(tail)
    print("[gate2] rc=%d total %.0fs" % (rc, time.time() - t0))
    return rc


if __name__ == "__main__":
    sys.exit(main())
