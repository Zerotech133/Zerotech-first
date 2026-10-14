# -*- coding: utf-8 -*-
"""W5c 勘误最小复算：修复后 replay 态主口径逐笔指标（含 PF）。

只重算受影响的"态0档案重放"行：missing（修复前 replay≡missing 的口径）
vs replay（修复后真实注入）。主口径 = w_cur / 75-50 阈 / 闸门开。
产出 reports/w5c_jevkey_recalc_20261014.json。

运行：
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/_w5c_recalc_jevkey.py
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qpzm_backtest as anchor   # noqa: E402

OUT_JSON = os.path.join(
    HERE, "reports", "w5c_jevkey_recalc_20261014.json")
START = 20251001
COST = 0.0015
HOLDS = (5, 10, 20)


def profit_factor(ret_s):
    if len(ret_s) == 0:
        return None
    gain = float(ret_s[ret_s > 0].sum())
    loss = float(-ret_s[ret_s < 0].sum())
    if loss <= 0:
        return None
    return round(gain / loss, 4)


def main():
    t0 = time.time()
    print("[1/3] load_wide …", flush=True)
    wide = anchor.load_wide(os.path.join(HERE, "data", "ashare"))
    union, codes = wide["dates"], wide["codes"]
    in_win = union >= START
    eligible = wide["eligible"]

    print("[2/3] env / rps / tech …", flush=True)
    env_df = anchor.env_gate(wide["above20"], wide["ret20"])
    mix_cur, _na = anchor.cross_rps(
        {p: wide["chg%d" % p] for p in anchor.PERIODS}, anchor.RPS_W_CUR)
    tech_w = anchor.tech_score(wide["longs"], wide["above20"])

    jev_mat, jev_meta = anchor.load_jev_replay()

    print("[3/3] 基准 + 两态回放 …", flush=True)
    bench_by_hold = {
        n: anchor.bench_returns(
            eligible.loc[in_win], wide["open"].loc[in_win],
            wide["close"].loc[in_win], n, COST, unfill=wide["unfill"])
        for n in HOLDS}

    arms = {}
    for name, mode in (("missing", "missing"), ("replay_fixed", "replay")):
        _sc, buy, _ob = anchor.combine_score(
            tech_w, mix_cur, env_df, buy_th=75.0, obs_th=50.0,
            gate_on=True, eligible=eligible, jev_mode=mode,
            jev_mat=jev_mat)
        buy_w = buy.loc[in_win]
        arms[name] = {"n_buy": int(buy_w.sum().sum()), "holds": {}}
        for n in HOLDS:
            ret, ndrop = anchor.trade_returns(
                buy_w, wide["open"].loc[in_win],
                wide["close"].loc[in_win], n, COST,
                unfill=wide["unfill"])
            dm = ret.index.get_level_values("date")
            bench = bench_by_hold[n]
            bench = bench[bench.index.get_level_values("date")
                          .isin(set(dm.unique()))]
            arms[name]["holds"][str(n)] = {
                "n_trades": int(len(ret)),
                "n_dropped_nobar": ndrop,
                "mean": round(float(ret.mean()), 6) if len(ret) else None,
                "median": round(float(ret.median()), 6),
                "win_rate": round(float((ret > 0).mean()), 4),
                "pf": profit_factor(ret),
                "max_dd": round(anchor.max_drawdown(
                    anchor.date_mean(ret)), 4),
                "excess_mean": round(
                    float(ret.mean()) - float(bench.mean()), 6),
            }
            print("  %-12s hold%2d mean=%s pf=%s" % (
                name, n, arms[name]["holds"][str(n)]["mean"],
                arms[name]["holds"][str(n)]["pf"]), flush=True)

    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "interpreter": sys.executable,
            "task": "W5c 键形态修复后 replay 主口径最小复算（勘误数字）",
            "window": {"start": START, "end": None,
                       "signal_days": int(in_win.sum())},
            "n_stocks": len(codes),
            "caliber": "w_cur / buy75-obs50 / gate_on / T+1 开盘买 / "
                       "往返成本 0.15% / K254 后复权 / 一字板剔除",
            "jev_replay_meta": jev_meta,
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "arms": arms,
    }
    anchor.atomic_write_json(OUT_JSON, payload)
    print("report -> %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
