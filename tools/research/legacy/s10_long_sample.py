# -*- coding: utf-8 -*-
"""S10 长样本验证（2015-2026）：低波+反转（+换手 2023+）行业中性 top20。

数据：daily adj close（2014-07 起缓冲）；daily_basic turnover 仅 2023+（缺失时用 3 因子）。
输出：research/reports/s10_long_sample_<tag>.{json,md}
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pit_utils import listed_days  # noqa: E402

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
COST = 0.0015
TOPN = 20


def load_adj(dts: list[str]) -> pd.DataFrame:
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


def z(s: pd.Series) -> pd.Series:
    s = s.replace([np.inf, -np.inf], np.nan)
    mu, sd = s.mean(), s.std()
    return (s - mu) / sd if sd and sd > 0 else s * 0


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    dts = [d for d in ds if d >= "20140701"]
    adj = load_adj(dts)
    dates = adj.index.tolist()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    def listed_at(dt_):
        return listed_days(sb, dt_)
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20150101"]
    print("panel", adj.shape, "months", len(mends), mends[0], "..", mends[-1], flush=True)

    def run(period_start: str, use_turn: bool) -> dict:
        ms = [m for m in mends if m >= period_start]
        prev = set(); rows = []
        for j in range(len(ms) - 1):
            dt, nxt = ms[j], ms[j + 1]
            if dt not in adj.index or nxt not in adj.index:
                continue
            i = dates.index(dt)
            c = adj.iloc[i]
            uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                  (listed_at(dt).reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
            cc = c.index[uni.fillna(False)]
            vol = -adj.iloc[i - 20:i + 1].pct_change().std().reindex(cc)
            m20 = -(c / adj.iloc[i - 20] - 1).reindex(cc)
            m120 = -(c / adj.iloc[i - 120] - 1).reindex(cc)
            df = pd.DataFrame({"vol": z(vol), "m20": z(m20), "m120": z(m120)})
            if use_turn:
                tparts = []
                for d in dates[max(0, i - 20): i + 1]:
                    f = DATA / "daily_basic" / ("d_" + d + ".parquet")
                    if f.exists():
                        tparts.append(pq.read_table(f, columns=["ts_code", "turnover_rate"]).to_pandas().set_index("ts_code")["turnover_rate"])
                if tparts:
                    tm = pd.concat(tparts, axis=1)
                    tm = tm[~tm.index.duplicated(keep="last")].mean(axis=1)
                    df["turn"] = z(-tm.reindex(cc))
            score = df.mean(axis=1, skipna=True)
            g = score.groupby(industry.reindex(score.index))
            score = ((score - g.transform("mean")) / g.transform("std").replace(0, np.nan)).dropna()
            picks = score.sort_values(ascending=False).head(TOPN).index
            if len(picks) < 5:
                continue
            r = adj.iloc[dates.index(nxt)] / c - 1
            pr = float(r.reindex(picks).mean())
            mkt = float(r.reindex(cc).mean())
            turnover = len(set(picks) - prev) / len(picks)
            pr -= turnover * COST * 2
            rows.append({"year": nxt[:4], "ret": pr, "market": mkt})
            prev = set(picks)
        df = pd.DataFrame(rows)
        nav = (1 + df["ret"]).cumprod()
        yrs = len(df) / 12
        yr = df.groupby("year")["ret"].apply(lambda g2: float((1 + g2).prod() - 1))
        ex = df["ret"] - df["market"]
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
        return {"months": len(df), "cagr_pct": round(float(nav.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / nav.cummax() - 1).min()) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2),
                "positive_years": f"{int((yr > 0).sum())}/{len(yr)}",
                "yearly": {str(y): round(float(v) * 100, 1) for y, v in yr.items()}}

    out = {
        "3f_2015": run("20150101", False),
        "3f_2019": run("20190101", False),
        "3f_2023": run("20230101", False),
        "4f_2023": run("20230101", True),
    }
    market = {}
    mkt_ret = adj.pct_change().mean(axis=1)
    for key, st in (("market_2015", "20150101"), ("market_2019", "20190101"), ("market_2023", "20230101")):
        m = mkt_ret[mkt_ret.index >= mends[0]]
        m = m[m.index >= st]
        nav = (1 + m).cumprod()
        yrs = len(m) / 252
        market[key] = {"cagr_pct": round(float(nav.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
                        "mdd_pct": round(float((nav / nav.cummax() - 1).min()) * 100, 2),
                        "vol_pct": round(float(m.std() * np.sqrt(252)) * 100, 2)}
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "data": "daily adj close 2014-07~2026-10；turnover 仅2023+",
                    "rule": "z(-vol20)+z(-ret20)+z(-ret120)(+z(-turn20)) 均值；行业中性；top20；月频；成本0.15%×2×换手"},
           "market": market, "results": out}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"s10_long_sample_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = ["# S10 长样本验证（2015-2026）", "",
         f"- 配置：行业中性 3 因子（低波+20日反转+120日反转）top20 月频；2023+ 含换手为 4 因子",
         "- 成本 0.15% 单边 × 换手",
         "", "| 样本 | 因子 | CAGR | MDD | 月均超额 | t | 正收益年 |", "|---|---|---|---|---|---|---|"]
    for k, v in out.items():
        L.append(f"| {k} | {'4' if k.startswith('4f') else '3'} | {v['cagr_pct']}% | {v['mdd_pct']}% | {v['excess_monthly_pct']}% | {v['excess_t']} | {v['positive_years']} |")
    L += ["", "| 市场基准 | CAGR | MDD | vol |", "|---|---|---|---|"]
    for k, v in market.items():
        L.append(f"| {k} | {v['cagr_pct']}% | {v['mdd_pct']}% | {v['vol_pct']}% |")
    L += ["", "## 年度收益（3f_2015）", "", "| 年 | 收益 |", "|---|---|"]
    for y, v in out["3f_2015"]["yearly"].items():
        L.append(f"| {y} | {v}% |")
    L += ["", "## 读法", "- 若 2015 样本显著弱于 2019+ → 该基准可能依赖 2019 后 regime；",
          "- 4f_2023 与 3f_2023 对比 → 换手因子增量；", "- 长样本含 2015 股灾/2018 熊市，可看极端 regime 表现。"]
    (OUT / f"s10_long_sample_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"results": {k: {"cagr": v["cagr_pct"], "mdd": v["mdd_pct"], "t": v["excess_t"]} for k, v in out.items()},
                      "market": market}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
