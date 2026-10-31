# -*- coding: utf-8 -*-
"""S01 中性化检验：剥离市值/行业暴露后，H14 质量陷阱过滤是否有 alpha。

方法（逐日）：
  1. 全市场股票按「流通市值十分位 × 行业」分桶；
  2. 市场等权基准：每桶总权重 = 该桶股票数 / 全市场股票数；
  3. S01 中性组合：每桶内只持有通过 S01 过滤的股票，桶内等权、桶总权重与市场相同
     （即市值/行业暴露与市场完全一致）；
  4. 组合日收益 = Σ w_i × 次日 pct_chg；市场日收益 = 全市场 pct_chg 均值；
  5. 20 个交易日回填；成本近似 5bp/日（每日再平衡）、另给 0 成本版；
  6. 输出：中性 S01 vs 市场（纯过滤效果）、覆盖度、换手、MDD。

产出：research/reports/S01_neutral_20261031.{md,json}
"""
from __future__ import annotations

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
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine as se  # noqa: E402

COST_DAILY = 0.0005  # 每日再平衡近似 5bp


def dates_n(n: int) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


def day_frame(dt: str) -> pd.DataFrame:
    d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                      columns=["ts_code", "close", "pct_chg", "amount"]).to_pandas()
    db = pq.read_table(DATA / "daily_basic" / f"d_{dt}.parquet",
                       columns=["ts_code", "pe_ttm", "circ_mv"]).to_pandas()
    d = d.merge(db, on="ts_code", how="left")
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas()
    return d.merge(sb[["ts_code", "name", "industry", "list_date"]], on="ts_code", how="left")


def eligible_mask(u: pd.DataFrame, date: str, fin: pd.DataFrame) -> pd.Series:
    u = u.merge(fin, on="ts_code", how="left")
    days = (pd.to_datetime(date) - pd.to_datetime(u["list_date"], format="%Y%m%d", errors="coerce")).dt.days
    drops = {
        "st": u["name"].fillna("").str.contains(r"ST|\*ST|退", regex=True),
        "new": days.fillna(0) < 250,
        "price": u["close"].fillna(0) < 2.0,
        "liquidity": u["amount"].fillna(0) * 1000.0 < 5e7,
        "limitup": u["pct_chg"].fillna(0) >= 9.5,
        "missing": u["pe_ttm"].isna() | u["roe"].isna() | (u["pe_ttm"] <= 0),
        "loss": u["roe"] <= 0,
    }
    ok = ~pd.concat(drops.values(), axis=1).any(axis=1)
    # H14 陷阱剔除（高估值层 + ROE 层内前 20%）
    tmp = u[ok].copy()
    if len(tmp):
        tmp["pe_bucket"] = pd.qcut(tmp["pe_ttm"], 3, labels=["低", "中", "高"], duplicates="drop")
        tmp["roe_pct"] = tmp.groupby("pe_bucket", observed=True)["roe"].rank(pct=True)
        trap = (tmp["pe_bucket"].astype(str) == "高") & (tmp["roe_pct"] >= 0.8)
        ok.loc[tmp.index[trap]] = False
    return ok


def main() -> int:
    dts = dates_n(22)
    rows = []
    prev_w = {}
    for i in range(len(dts) - 1):
        d, nd = dts[i], dts[i + 1]
        u = day_frame(d)
        fin = se.load_latest_fina(d)
        ok = eligible_mask(u, d, fin)
        u = u.assign(eligible=ok)
        # 分桶：市值十分位 × 行业
        u["size_dec"] = pd.qcut(u["circ_mv"], 10, labels=False, duplicates="drop")
        u["bucket"] = u["size_dec"].astype(str) + "|" + u["industry"].fillna("未知")
        counts = u.groupby("bucket").size()
        market_bucket_w = counts / counts.sum()
        elig = u[u["eligible"]]
        elig_counts = elig.groupby("bucket").size()
        weights = {}
        for b, c in elig_counts.items():
            if c <= 0 or b not in market_bucket_w.index:
                continue
            w_b = float(market_bucket_w[b])
            for code in elig[elig["bucket"] == b]["ts_code"]:
                weights[code] = w_b / c
        # 次日收益
        ndf = pq.read_table(DATA / "daily" / f"d_{nd}.parquet", columns=["ts_code", "pct_chg"]).to_pandas().set_index("ts_code")["pct_chg"] / 100.0
        port_ret = sum(w * float(ndf.get(c, 0.0) or 0.0) for c, w in weights.items())
        mkt_ret = float(ndf.mean())
        invested = sum(weights.values())
        turnover = sum(abs(weights.get(c, 0.0) - prev_w.get(c, 0.0)) for c in set(weights) | set(prev_w))
        prev_w = weights
        rows.append({"date": nd, "port": port_ret - COST_DAILY, "port_gross": port_ret,
                     "market": mkt_ret, "invested": round(invested, 4), "turnover": round(turnover, 3),
                     "n_hold": len(weights)})
    df = pd.DataFrame(rows)
    df["excess"] = df["port_gross"] - df["market"]

    def cum(s):
        return float(np.prod(1 + s) - 1)

    nav_p = (1 + df["port"]).cumprod()
    mdd = float((nav_p / nav_p.cummax() - 1).min())
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "days": len(df), "cost_daily_bp": COST_DAILY * 10000},
           "market_ret_pct": round(cum(df["market"]) * 100, 2),
           "s01_neutral_ret_pct": round(cum(df["port"]) * 100, 2),
           "s01_neutral_gross_pct": round(cum(df["port_gross"]) * 100, 2),
           "excess_gross_pct": round(cum(df["port_gross"]) * 100 - cum(df["market"]) * 100, 2),
           "mdd_pct": round(mdd * 100, 2),
           "avg_invested": round(float(df["invested"].mean()), 4),
           "avg_turnover": round(float(df["turnover"].mean()), 3),
           "avg_hold": round(float(df["n_hold"].mean()), 1),
           "daily": df.round(6).to_dict(orient="records")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "S01_neutral_20261031.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# S01 中性化检验（剥离市值/行业暴露）", "",
         f"- 窗口：{len(df)} 个交易日；成本近似 {COST_DAILY*10000:.0f}bp/日（另给 0 成本版）",
         f"- 市场等权：{rep['market_ret_pct']}%",
         f"- S01 中性（净）：{rep['s01_neutral_ret_pct']}%；毛：{rep['s01_neutral_gross_pct']}%",
         f"- 纯过滤超额（毛）：{rep['excess_gross_pct']}%", "",
         f"- 平均覆盖（投资比例）：{rep['avg_invested']:.1%}；平均持仓 {rep['avg_hold']} 只；日均换手 {rep['avg_turnover']}",
         f"- 最大回撤：{rep['mdd_pct']}%", "",
         "## 结论",
         "- 若中性化后超额≈0 → S01 过滤无 alpha（此前跑赢是市值倾斜）；",
         "- 若中性化后仍显著正 → H14 陷阱剔除有效，可保留为过滤器。"]
    (OUT / "S01_neutral_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("market_ret_pct", "s01_neutral_ret_pct", "excess_gross_pct",
                                          "mdd_pct", "avg_invested", "avg_hold", "avg_turnover")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
