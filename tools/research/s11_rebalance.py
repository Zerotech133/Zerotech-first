# -*- coding: utf-8 -*-
"""S11 影子：mom_120 反转 + 下行 regime 过滤（60日市场收益<-2%），K20 分数加权，月频。

依据：H39 四关 + H52 稳健网格 + regime 阈值敏感性（18 组合 t 1.84-2.56）。
幂等：月频调仓；非下行 regime 空仓；账本记录期收益/市场/超额/活跃状态。
产出：research/shadow/s11/positions_<sig>.json、ledger.jsonl；portal/static/s11_shadow.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
SHADOW = REPO / "research" / "shadow" / "s11"
STATIC = REPO / "portal" / "static"
K = 20
LOOKBACK = 60
THRESHOLD = -0.02


def load_close(dts: list[str]) -> pd.DataFrame:
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / ("d_" + dt + ".parquet"), columns=["ts_code", "close"]).to_pandas()
        af = DATA / "adj_factor" / ("d_" + dt + ".parquet")
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "adj"]])
        if len(parts) >= 400:
            ch = pd.concat(parts, ignore_index=True); parts = []
            piv = ch.pivot_table(index="trade_date", columns="ts_code", values="adj")
            if "acc" not in locals():
                acc = piv
            else:
                acc = pd.concat([acc, piv], axis=0)
    if parts:
        ch = pd.concat(parts, ignore_index=True)
        piv = ch.pivot_table(index="trade_date", columns="ts_code", values="adj")
        if "acc" not in locals():
            acc = piv
        else:
            acc = pd.concat([acc, piv], axis=0)
    acc.index = [str(x) for x in acc.index]
    acc = acc.loc[:, ~acc.columns.duplicated()]
    return acc.sort_index().astype("float32")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    dts = ds[-320:]
    latest = dts[-1]
    sig_files = sorted(SHADOW.glob("positions_*.json")) if SHADOW.exists() else []
    prev = json.loads(sig_files[-1].read_text(encoding="utf-8")) if sig_files else None
    prev_sig = prev.get("signal_date") if prev else None
    rebalance = (prev is None) or (prev_sig != latest and prev_sig[:6] != latest[:6])
    if args.dry_run:
        print(json.dumps({"latest": latest, "prev_sig": prev_sig, "rebalance": rebalance}, ensure_ascii=False))
        return 0
    adj = load_close(dts)
    mret = adj.pct_change().mean(axis=1)
    mkt = (1 + mret).cumprod()
    m60 = float(mkt.iloc[-1] / mkt.iloc[-LOOKBACK] - 1)
    active = m60 < THRESHOLD
    pos = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "strategy": "S11_MOM120_REGIME_LONG", "signal_date": latest,
           "regime": {"lookback": LOOKBACK, "threshold_pct": THRESHOLD * 100, "market_ret_pct": round(m60 * 100, 2),
                       "active": bool(active)},
           "rule": "市场等权60日收益<-2% 才持仓；-ret120 分数 top20，按分数排名加权；非下行月空仓"}
    if rebalance:
        if active:
            c = adj.iloc[-1]
            sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
            names = sb["name"]
            list_dates = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
            uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                  ((pd.Timestamp(latest) - list_dates).dt.days.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna() & \
                  ~c.index.str.endswith(".BJ")  # 排除北交所
            cc = c.index[uni.fillna(False)]
            score = (-(c / adj.iloc[-121] - 1)).reindex(cc).dropna()
            picks = score.sort_values(ascending=False).head(K)
            r = np.arange(len(picks), 0, -1, dtype=float); w = r / r.sum()
            amt = pq.read_table(DATA / "daily" / ("d_" + latest + ".parquet"), columns=["ts_code", "amount"]).to_pandas().set_index("ts_code")["amount"]
            cap = float(np.nansum(amt.reindex(picks.index).values)) * 1000 * 0.05 / 1e8
            pos["holdings"] = [{"ts_code": c2, "name": names.get(c2), "score": round(float(s), 4),
                                 "w": round(float(ww), 4), "close": round(float(c[c2]), 2)} for (c2, s), ww in zip(picks.items(), w)]
            pos["n"] = int(len(pos["holdings"]))
            pos["capacity5_yi"] = round(cap, 2)
        else:
            pos["holdings"] = []
            pos["n"] = 0
            pos["capacity5_yi"] = 0.0
        SHADOW.mkdir(parents=True, exist_ok=True)
        (SHADOW / f"positions_{latest}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
        prev = pos; prev_sig = latest
        print("rebalanced", latest, "active", active, "n", pos["n"], "cap", pos["capacity5_yi"], flush=True)
    # 期收益
    cur = mkt_r = None
    if prev and prev_sig and prev_sig in dts:
        p0 = adj.loc[prev_sig]; p1 = adj.loc[latest]
        holds = [h["ts_code"] for h in prev.get("holdings") or []]
        if holds:
            rr = [(p1[c2] / p0[c2] - 1) for c2 in holds if c2 in p0.index and c2 in p1.index]
            cur = float(np.mean(rr)) if rr else None
        else:
            cur = 0.0
        mkt_r = float((p1 / p0 - 1).mean())
    ledger = []
    lf = SHADOW / "ledger.jsonl"
    if lf.exists():
        ledger = [json.loads(x) for x in lf.read_text(encoding="utf-8").splitlines() if x.strip()]
    if cur is not None and (not ledger or ledger[-1]["date"] != latest):
        row = {"date": latest, "from": prev_sig, "ret": round(cur, 6), "market": round(mkt_r or 0.0, 6),
               "excess": round(cur - (mkt_r or 0.0), 6), "active": bool(prev.get("holdings")), "n": prev.get("n", 0)}
        ledger.append(row)
        with lf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    static = STATIC / "s11_shadow.json"
    pub = json.loads(static.read_text(encoding="utf-8")) if static.exists() else {}
    pub.update(prev)
    pub["ledger"] = ledger[-24:]
    pub["tracking"] = {"since": prev_sig, "cur_ret_pct": round(float(cur or 0) * 100, 2),
                       "market_pct": round(float(mkt_r or 0) * 100, 2),
                       "excess_pct": round(float((cur or 0) - (mkt_r or 0)) * 100, 2)}
    pub["next_rebalance"] = "下一个月末（每月 1 日 08:30 检查 regime）"
    pub["status"] = "shadow_observing_small"
    pub["live_cap_yi"] = 1.5
    pub["disclaimer"] = "研究/影子（容量上限1.5亿），不构成投资建议"
    STATIC.mkdir(parents=True, exist_ok=True)
    static.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"latest": latest, "signal": prev_sig, "active": active, "tracking": pub["tracking"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
