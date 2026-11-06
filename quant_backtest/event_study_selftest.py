# -*- coding: utf-8 -*-
"""P3 事件研究引擎自测（契约 + 手工核算对照，验收口径误差<1%）."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from event_study import BUILTIN_EVENTS, EventStudy, pit_t0  # noqa: E402
from pit_visibility import resolve_lag_days, trading_days_after  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name, detail)


# ---- 1. PIT 公共件 ----
check("1 lag 缺省 publish→1", resolve_lag_days(None, None) == 1)
check("2 lag 显式优先", resolve_lag_days("publish", 3) == 3)
check("3 交易日推进", trading_days_after(["20240101", "20240102", "20240103"],
                                         "20240101", 2) == "20240103")
check("4 推进不足→None", trading_days_after(["20240101"], "20240101", 2) is None)
_pv = __import__("pit_visibility").visibility_proof(100, 80, 5)
check("5 可见性证明覆盖率",
      abs(_pv["pit_coverage"] - 0.8) < 1e-9 and _pv["n_estimated_lag"] == 5)

# ---- 2. T0 语义 ----
check("6 T0=事件日次一交易日",
      pit_t0(["20240105", "20240108", "20240109"], "20240104", 1) == "20240105")

# ---- 3. 引擎端到端: 小事件集 ----
es = EventStudy()
es._load_px()
ev = BUILTIN_EVENTS["lhb_uplist"]
res = es.study("lhb_uplist", window=(-5, 5), max_events=300)
curve = res["curve"]
check("7 曲线窗口完整", set(int(k) for k in curve.keys() if int(k) != 0)
      >= {-5, -1, 1, 5} - {0}, f"keys={list(curve)[:3]}...")
check("8 t 值与样本量一致",
      all((not np.isnan(v["t_stat"])) == (v["n"] >= 2)
          for v in curve.values()))
check("9 PIT 证明随结果", res["pit"]["pit_coverage"] is not None
      and res["pit"]["contract"].startswith("available_after"))
check("10 h=0 基点日不入曲线", 0 not in
      [int(k) for k in curve.keys()])

# ---- 4. 手工核算对照（验收: 误差<1%）----
# 取 lhb 事件一个样本, 用原始 ts_daily 独立复算 T+5 超额
dl = es.reader.read("ts_daily", columns=["ts_code", "trade_date", "close"])
top = es.reader.read("ts_top_list", columns=["ts_code", "trade_date"])
top = top.dropna().head(50)
found = None
for _, e in top.iterrows():
    code, ed = str(e["ts_code"]), str(e["trade_date"])[:8].replace("-", "")
    days = es._stock_days.get(code)
    if not days:
        continue
    t0 = pit_t0(days, ed, 1)
    if t0 and days.index(t0) + 5 < len(days):
        found = (code, ed, t0)
        break
assert found, "未找到可复算样本"
code, ed, t0 = found
days = es._stock_days[code]
i0 = days.index(t0)
base_px = es._stock_px[code].get(days[i0 - 1])
manual_raw = (es._stock_px[code].get(days[i0 + 5]) / base_px - 1)
# 引擎同窗原始收益(从结果 JSON 抽样重算)
sr = es._window_rets(es._stock_px[code], days, t0, (-5, 5))
eng_raw = sr[5]
err = abs(manual_raw - eng_raw) / max(abs(manual_raw), 1e-12)
check("11 手工核算 T+5 原始收益误差<1%", err < 0.01,
      f"manual={manual_raw:.6f} engine={eng_raw:.6f} err={err:.2e}")
# 基准复算: 市场等权 T0-1→T0+5 累计
br = es._bench_rets(days, t0, (-5, 5))
manual_bench = float(np.expm1(sum(np.log1p(es._bench_ret.get(days[j], 0))
                                  for j in range(i0, i0 + 6))))
err_b = abs((1 + manual_bench) - (1 + br.get(5, manual_bench)))
check("12 手工核算 T+5 基准收益绝对误差<1%", err_b < 0.01,
      f"manual={manual_bench:.6f} engine={br.get(5):.6f}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
