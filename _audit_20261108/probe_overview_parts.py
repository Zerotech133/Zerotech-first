# -*- coding: utf-8 -*-
"""overview 各分项耗时剖面：决定拆分边界（重项后台刷新 / 轻项同步返回）。"""
import sys
import time

sys.path.insert(0, r"C:\项目\老田的AI助手\portal")

import api_misc as M

PARTS = ["get_containers", "get_tasks", "get_backups", "get_alerts",
         "get_disks", "get_news", "get_quotes", "get_backtest",
         "get_gzh", "get_video"]

rows = []
for name in PARTS:
    fn = getattr(M, name, None)
    if fn is None:
        rows.append((name, None, "缺失"))
        continue
    t0 = time.perf_counter()
    try:
        fn()
        err = ""
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
    rows.append((name, time.perf_counter() - t0, err))

t0 = time.perf_counter()
for port in (8734, 8105, 9104, 11434):
    M.tcp_ok(port)
tcp = time.perf_counter() - t0
rows.append(("tcp_ok x4", tcp, ""))

t0 = time.perf_counter()
M.build_overview()
total = time.perf_counter() - t0

rows.sort(key=lambda r: (r[1] is not None, -(r[1] or 0)))
print("%-16s %9s  %s" % ("分项", "耗时(s)", "异常"))
for name, sec, err in rows:
    print("%-16s %9s  %s" % (name, "n/a" if sec is None else "%.3f" % sec, err))
print("-" * 40)
print("%-16s %9.3f" % ("build_overview 合计", total))
heavy = sum(r[1] for r in rows if r[1] is not None and r[1] >= 0.2)
print("其中 >=0.2s 的重项合计 %.3fs（占 %.0f%%）"
      % (heavy, 100.0 * heavy / total if total else 0))
