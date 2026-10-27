# -*- coding: utf-8 -*-
"""325-D 冒烟：部分宇宙（tushare 已回补票）双刀全网 14:45 首次真实信号。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest_engine_v2 import calc_dknife_signals_market  # noqa: E402

sig, pool, meta, err = calc_dknife_signals_market("20260915", "20261012", hist_st=True)
if err:
    print("ERR:", err)
    sys.exit(1)
print("universe:", meta["universe"], "只 | 快照源:", meta["source"])
print("hist_st:", meta["hist_st"])
print("信号日数:", len(sig), "| 信号总数:", meta["n_sig"])
for day in sorted(sig)[-8:]:
    print(day, "->", len(sig[day]), "只:", sorted(sig[day])[:6])
