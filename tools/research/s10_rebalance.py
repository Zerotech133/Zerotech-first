# -*- coding: utf-8 -*-
"""S10 证据基准影子：低波+反转+低换手（行业中性）top20 等权，月频。

依据：H49 基准审查 + H50 四关通过（CAGR 35.17%/MDD -23.09%/t 3.10/容量 1.61亿/双regime正）。
月频调仓；幂等；T+1 起持有；账本记录期收益/超额/容量。
用法：python tools/research/s10_rebalance.py [--dry-run]
产出：research/shadow/s10/positions_<sig>.json、ledger.jsonl；portal/static/s10_shadow.json
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
SHADOW = REPO / "research" / "shadow" / "s10"
STATIC = REPO / "portal" / "static"
TOPN = 20


def dates_last(n: int = 320) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


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
    raw = pd.concat(parts, ignore_index=True)
    adj = raw.pivot_table(index="trade_date", columns="ts_code", values="adj")
    adj.index = [str(x) for x in adj.index]
    adj = adj.loc[:, ~adj.columns.duplicated()]
    return adj.sort_index().astype("float32")


def zscore(s: pd.Series) -> pd.Series:
    s = s.replace([np.inf, -np.inf], np.nan)
    mu, sd = s.mean(), s.std()
    return (s - mu) / sd if sd and sd > 0 else s * 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    dts = dates_last()
    latest = dts[-1]
    sig_files = sorted(SHADOW.glob("positions_*.json")) if SHADOW.exists() else []
    prev = json.loads(sig_files[-1].read_text(encoding="utf-8")) if sig_files else None
    prev_sig = prev.get("signal_date") if prev else None
    # 数据截止到 latest：若没有持仓或跨月则视为月末调仓
    is_month_end = True
    rebalance = (prev is None) or (prev_sig != latest and prev_sig[:6] != latest[:6])
    if args.dry_run:
        print(json.dumps({"latest": latest, "prev_sig": prev_sig, "rebalance": rebalance}, ensure_ascii=False))
        return 0
    if rebalance:
        adj = load_close(dts[-(200 + 130):])
        c = adj.iloc[-1]
        sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
        names, industry = sb["name"], sb["industry"].fillna("未知")
        listed = (pd.Timestamp(latest) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
        uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
        cc = c.index[uni.fillna(False)]
        vol = -adj.iloc[-21:].pct_change().std().reindex(cc)
        m20 = -(c / adj.iloc[-21] - 1).reindex(cc)
        m120 = -(c / adj.iloc[-121] - 1).reindex(cc)
        to = None
        recent = [d for d in dts[-30:]]
        tparts = []
        for d in recent:
            f = DATA / "daily_basic" / ("d_" + d + ".parquet")
            if f.exists():
                tparts.append(pq.read_table(f, columns=["ts_code", "turnover_rate"]).to_pandas().set_index("ts_code")["turnover_rate"])
        if tparts:
            dm = pd.concat(tparts, axis=1)
            dm = dm[~dm.index.duplicated(keep="last")]
            to = -dm.mean(axis=1).reindex(cc)
        df = pd.DataFrame({"vol": zscore(vol), "m20": zscore(m20), "m120": zscore(m120)})
        if to is not None:
            df["turn"] = zscore(to)
        score = df.mean(axis=1, skipna=True)
        g = score.groupby(industry.reindex(score.index))
        score = ((score - g.transform("mean")) / g.transform("std").replace(0, np.nan)).dropna()
        picks = score.sort_values(ascending=False).head(TOPN)
        w = 1.0 / len(picks)
        amt = pq.read_table(DATA / "daily" / ("d_" + latest + ".parquet"), columns=["ts_code", "amount"]).to_pandas().set_index("ts_code")["amount"]
        cap = float(np.nansum(amt.reindex(picks.index).values)) * 1000.0 * 0.05 / 1e8
        pos = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "strategy": "S10_EVIDENCE_BASELINE_NEUTRAL", "signal_date": latest, "n": int(len(picks)),
               "weight_per": round(w, 4), "capacity5_yi": round(cap, 2),
               "rule": "z(-vol20)+z(-ret20)+z(-ret120)+z(-turnover20) 均值；行业内 z 中性；top20 等权；月频",
               "holdings": [{"ts_code": c2, "name": names.get(c2), "industry": industry.get(c2),
                              "score": round(float(s), 4), "w": round(w, 4), "close": round(float(c[c2]), 2)}
                             for c2, s in picks.items()]}
        SHADOW.mkdir(parents=True, exist_ok=True)
        (SHADOW / f"positions_{latest}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
        prev = pos; prev_sig = latest
        print("rebalanced:", latest, "n=", len(picks), "cap5=", round(cap, 2), flush=True)
    # 期收益（上一信号→最新）
    cur = mkt = None
    if prev and prev_sig and prev_sig in dts:
        adj2 = load_close([prev_sig, latest])
        p0, p1 = adj2.loc[prev_sig], adj2.loc[latest]
        rr = [(p1[c2] / p0[c2] - 1) for c2 in [h["ts_code"] for h in prev["holdings"]] if c2 in p0.index and c2 in p1.index]
        cur = float(np.mean(rr)) if rr else None
        univ = p1 / p0 - 1
        mkt = float(univ.mean())
    # 账本
    ledger = []
    lf = SHADOW / "ledger.jsonl"
    if lf.exists():
        ledger = [json.loads(x) for x in lf.read_text(encoding="utf-8").splitlines() if x.strip()]
    if cur is not None and (not ledger or ledger[-1]["date"] != latest):
        row = {"date": latest, "from": prev_sig, "ret": round(cur, 6), "market": round(mkt, 6),
               "excess": round(cur - mkt, 6), "n": prev["n"]}
        ledger.append(row)
        with lf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("ledger:", row, flush=True)
    static = STATIC / "s10_shadow.json"
    pub = json.loads(static.read_text(encoding="utf-8")) if static.exists() else {}
    pub.update(prev)
    pub["ledger"] = ledger[-24:]
    pub["tracking"] = {"since": prev_sig, "cur_ret_pct": round(float((cur or 0)) * 100, 2),
                       "market_pct": round(float((mkt or 0)) * 100, 2),
                       "excess_pct": round(float((cur or 0) - (mkt or 0)) * 100, 2)}
    pub["next_rebalance"] = "下一个月末（每月 1 日 08:50 检查）"
    pub["status"] = "shadow_observing"
    pub["disclaimer"] = "研究/影子，不构成投资建议"
    STATIC.mkdir(parents=True, exist_ok=True)
    static.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"latest": latest, "signal": prev_sig, "tracking": pub["tracking"], "ledger_n": len(ledger)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
