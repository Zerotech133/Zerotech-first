# -*- coding: utf-8 -*-
"""XqExpandedShard wrapper：与 run_xq_daily.py 同构，但只跑扩源分片。

把 selection_universe.json 中 R8/R10 扩源两源（houfeng_candidates164 + cohorts，
共 222 只去重）独立于 07:00 基线 run 在 21:00 错峰采集，保证扩源在 WAF 早停时仍生效。
分片 run 由调度显式指定 --sources，绕过 xq_include 开关（责任在运维侧）。
"""
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "xq_timeline_expanded.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
sys.stderr = sys.stdout
print("\n===== %s XqExpandedShard start =====" % time.strftime("%Y-%m-%d %H:%M:%S"),
      flush=True)
os.chdir(HERE)
sys.argv = ["xq_stock_timeline.py", "--sources",
            "houfeng_candidates164,cohorts", "--pages", "1"]
try:
    runpy.run_path(os.path.join(HERE, "xq_stock_timeline.py"), run_name="__main__")
    print("===== rc=0 =====", flush=True)
except SystemExit as e:
    print("===== rc=%s =====" % e.code, flush=True)
except Exception as e:  # noqa: BLE001
    print("===== rc=1 %s: %s =====" % (type(e).__name__, e), flush=True)
