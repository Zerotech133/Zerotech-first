# -*- coding: utf-8 -*-
"""退化副本验证：确认 pool_selfcheck 的断言真的会失败（回归工具，可反复跑）。

断言全部通过不等于断言有效——也可能它压根没检查到东西。这里人为把
产物改坏三处，看自检是否精确报出对应的三项。

退化项与应报的断言：
  1. 某只 death_px 抬到现价上方          → A1c FAIL
  2. 某只 death_disp_pct 改成 -60         → A1b FAIL
  3. 某只 3买 的 grade 改成 watch         → A4 + A5b FAIL
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "portal", "chanlun_pool_20261106.json")
TMP = os.path.join(ROOT, "portal", "_pool_degraded.json")


def main():
    with open(SRC, encoding="utf-8") as f:
        d = json.load(f)
    pool = d["pool"]

    # 1) 把第一只的生死线抬到现价上方
    pool[0]["death_px"] = round(pool[0]["px"] * 1.05, 3)
    # 2) 把第二只的 disp 改成 -60
    pool[1]["death_disp_pct"] = -60.0
    # 3) 把一只 3买 提到 watch
    for r in pool:
        if r["type"] == "3买" and r["zone"] != "above":
            r["grade"] = "watch"
            break

    with open(TMP, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

    r = subprocess.run([sys.executable, os.path.join(HERE, "pool_selfcheck.py"),
                        "--pool", TMP], capture_output=True, text=True)
    print("[退化] exit=%d" % r.returncode)
    fails = [ln for ln in r.stdout.splitlines() if "FAIL" in ln]
    for ln in fails:
        print("   " + ln.strip())
    print("\n预期：A1b / A1c / A4 各报一次；实际报 %d 项" % len(fails))
    os.remove(TMP)
    return 0 if (r.returncode == 1 and len(fails) >= 3) else 1


if __name__ == "__main__":
    sys.exit(main())
