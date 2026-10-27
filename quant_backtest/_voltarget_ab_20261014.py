# -*- coding: utf-8 -*-
"""vol targeting 对照实验（[20261014_voltarget]，一次性脚本不入产线）。

口径（如实标注）：
- 数据：data/ashare 湖日线 parquet（缩样 5 只 CN 大票，非全池）；
- 复权：adj_factor 湖只到 2023-07 不够覆盖 → 用 parquet 自带 pre_close
  链乘构造后复权 close（close_hfq(t)=close_hfq(t-1)*close_t/pre_close_t，
  首日=原 close；与 chanlun_scan._chain_ratio 同口径，最新根不归一不影响
  收益率）；
- 策略：strategies.REGISTRY 四策略之 trend_follow（runner 日常默认）；
- 成本：CN 分层口径（config trade.by_market.CN = 12+3 bps）+ T+1；
- vol_target：默认参数 target=0.15 / lookback=20 / floor=0.25 / cap=1.0；
- PF：trade_list 逐笔（缩放后 pnl），盈利笔合计 / 亏损笔合计绝对值。
"""
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest          # noqa: E402
import strategies as strat  # noqa: E402

SYMBOLS = ["600519.SH", "000001.SH", "600036.SH", "000858.SZ", "601318.SH"]
STRAT = "trend_follow"


def load_hfq(symbol):
    """ashare parquet → 后复权 df（pre_close 链乘口径）。"""
    p = os.path.join(HERE, "data", "ashare",
                     symbol.replace(".", "_") + ".parquet")
    df = pd.read_parquet(p).sort_values("trade_date")
    close = pd.to_numeric(df["close"], errors="coerce")
    pre = pd.to_numeric(df["pre_close"], errors="coerce")
    r = (close / pre).fillna(1.0).clip(0.05, 20.0)   # _chain_ratio 同守卫
    hfq = close.iloc[0] * r.cumprod()
    idx = pd.to_datetime(df["trade_date"].astype(str))
    return pd.DataFrame({"close": hfq.values}, index=idx)


def profit_factor(m):
    tl = m.get("trade_list") or []
    wins = sum(t["pnl"] for t in tl if t["pnl"] > 0)
    loss = abs(sum(t["pnl"] for t in tl if t["pnl"] < 0))
    return round(wins / loss, 3) if loss > 0 else float("inf") if wins else 0.0


def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    vt = backtest.load_vol_target_config(cfg)
    vt_off = dict(vt, enabled=False)
    vt_on = dict(vt, enabled=True)
    tc = backtest.load_trade_config("CN", cfg=cfg)
    params = dict(json.load(open(os.path.join(HERE, "config.json"),
                                 encoding="utf-8"))["strategies"]
                  .get(STRAT, {}))
    rows = []
    for sym in SYMBOLS:
        try:
            df = load_hfq(sym)
        except Exception as e:
            print("SKIP %s: %r" % (sym, e))
            continue
        if len(df) < 60:
            print("SKIP %s: bars=%d" % (sym, len(df)))
            continue
        sig = strat.REGISTRY[STRAT](df, params)
        m0 = backtest.run(df, sig, **tc, vol_target=vt_off)
        m1 = backtest.run(df, sig, **tc, vol_target=vt_on)
        rows.append({
            "symbol": sym, "bars": len(df),
            "off_ret": m0["total_return"], "on_ret": m1["total_return"],
            "off_pf": profit_factor(m0), "on_pf": profit_factor(m1),
            "off_dd": m0["max_drawdown"], "on_dd": m1["max_drawdown"],
            "off_trades": m0["trades"], "on_trades": m1["trades"],
            "off_sharpe": m0["sharpe"], "on_sharpe": m1["sharpe"],
        })
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    out = os.path.join(HERE, "_voltarget_ab_result_20261014.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"strat": STRAT, "cost": tc, "vt": vt, "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print("WROTE", out)


if __name__ == "__main__":
    main()
