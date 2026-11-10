# -*- coding: utf-8 -*-
"""portal 测试分批隔离执行器（审计用，不参与生产）。

为什么需要：
  全量 `pytest .` 在 Windows 上跑到 79% 后整体挂死（pytest-timeout 打印栈后
  终止进程，无 summary）。单独复现 `test_file_lock_open_gives_up_after_deadline`
  却 0.38s 通过 —— 说明是**测试间相互干扰**（前序用例的桩/线程/句柄泄漏），
  不是被测代码本身死锁。

  因此按文件起独立进程，隔离副作用，才能得到可用的失败清单。

用法：
  python _audit_20261108/audit_portal_tests.py [--jobs 1]
输出：
  _audit_20261108/portal_tests_result.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

PY = r"C:\Program Files\Python311\python.exe"
ROOT = r"C:\项目\老田的AI助手"
PORTAL = os.path.join(ROOT, "portal")
TESTS = os.path.join(PORTAL, "tests")
OUT = os.path.join(ROOT, "_audit_20261108", "portal_tests_result.json")

SUM_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


def run_one(path):
    cmd = [PY, "-m", "pytest", path, "-q", "--timeout=25",
           "-p", "no:cacheprovider", "--tb=no", "-rf"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=PORTAL, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=90)
        tail = (p.stdout or "").strip().splitlines()
        last = tail[-1] if tail else ""
        failed = [l for l in tail if l.startswith("FAILED")]
        counts = {k: int(n) for n, k in SUM_RE.findall(last)}
        return {
            "file": os.path.basename(path),
            "rc": p.returncode,
            "sec": round(time.time() - t0, 1),
            "counts": counts,
            "last": last[:200],
            "failed": [f[:160] for f in failed[:12]],
            "n_failed": len(failed),
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "file": os.path.basename(path), "rc": -9,
            "sec": round(time.time() - t0, 1), "counts": {},
            "last": "HANG >90s (killed)", "failed": [], "n_failed": 0,
            "timeout": True,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()

    files = sorted(
        os.path.join(TESTS, f) for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith(".py")
    )
    print("[audit] %d 个测试文件，进程隔离执行" % len(files))

    results = []
    for i, f in enumerate(files, 1):
        r = run_one(f)
        results.append(r)
        flag = "HANG" if r["timeout"] else ("FAIL" if r["n_failed"] else "ok")
        print("[%3d/%3d] %-46s %-4s %5.1fs %s"
              % (i, len(files), r["file"], flag, r["sec"],
                 json.dumps(r["counts"], ensure_ascii=False)), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "files": len(files), "results": results}, fh,
                  ensure_ascii=False, indent=1)

    bad = [r for r in results if r["n_failed"] or r["timeout"] or r["rc"] not in (0, 5)]
    print()
    print("[audit] 结论：%d 文件 / 有问题的 %d 个（失败或挂死或异常退出）"
          % (len(results), len(bad)))
    for r in bad:
        print("   %-46s rc=%-3s failed=%-3d %s"
              % (r["file"], r["rc"], r["n_failed"],
                 "HANG" if r["timeout"] else r["last"][:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
