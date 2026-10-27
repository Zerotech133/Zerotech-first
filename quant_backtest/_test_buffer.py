# -*- coding: utf-8 -*-
"""三买失效离场 buffer 0 vs 0.03 直接对照（in-process，排除 CLI 链路疑云）"""

import sys

sys.path.insert(0, r"C:/项目/老田的AI助手/quant_backtest")
import backtest_engine_v2 as eng

eng._ensure_market_loaded()
sig_buy, sig_sell, info, err = eng.calc_chanlun_signals_market(
    "20231010", "20241010", None, "3"
)
cfg = eng.BacktestConfig(name="t", strategy="chanlun", d0="20231010", d1="20241010")
for buf in (0.0, 0.03):
    r = eng.run_backtest(
        cfg,
        sig_buy,
        sig_sell,
        None,
        sig_zg=info["sig_zg"],
        three_fail=True,
        fail_level="zg",
        regime_gate=False,
        fail_buffer=buf,
    )
    n_fail = sum(1 for t in r["trades"] if t["how"] == "3b_fail")
    print(
        "buffer",
        buf,
        "→ trades",
        len(r["trades"]),
        "3b_fail",
        n_fail,
        "ret",
        r["summary"]["total_return_pct"],
    )
