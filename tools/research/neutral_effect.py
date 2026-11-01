# -*- coding: utf-8 -*-
"""行业中性化效应量化：raw z vs 行业内 z，对 5 个候选因子的月频 IC/多空影响。

因子：vol_20（低波方向）、mom_20、mom_120、rev_5、turnover_20（2023+）。
输出：research/reports/neutral_effect_<tag>.{json,md}
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
from sepa_backtest import load  # noqa: E402


def z(s: pd.Series) -> pd.Series:
    s = s.replace([np.inf, -np.inf], np.nan)
    mu, sd = s.mean(), s.std()
    return (s - mu) / sd if sd and sd > 0 else s * 0


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        """PIT：截至信号日的上市天数（2026-11-01 修复：原用最新日→次新股前视）"""
        return (pd.Timestamp(dt_) - list_dates_).dt.days
    turn = {}
    for f in sorted(glob.glob(str(DATA / "daily_basic" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if dt >= "20190101":
            turn[dt] = pq.read_table(f, columns=["ts_code", "turnover_rate"]).to_pandas().set_index("ts_code")["turnover_rate"]
    tdates = sorted(turn)
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]

    def ic_series(factor_fn) -> dict:
        raw_ics, neu_ics = [], []
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in P["a_close"].index or nxt not in P["a_close"].index:
                continue
            i = dates.index(dt)
            c = P["a_close"].iloc[i]
            fwd = P["a_close"].iloc[dates.index(nxt)] / c - 1
            uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                  (listed_at(dt).reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
            cc = c.index[uni.fillna(False)]
            f = factor_fn(i, c, cc)
            if f is None:
                continue
            d = pd.DataFrame({"raw": z(f), "y": fwd.reindex(cc)}).dropna()
            if len(d) < 200:
                continue
            raw_ics.append(float(d["raw"].corr(d["y"], method="spearman")))
            g = d.groupby(industry.reindex(d.index))
            neu = (d["raw"] - g["raw"].transform("mean")) / g["raw"].transform("std").replace(0, np.nan)
            d2 = pd.DataFrame({"neu": neu, "y": d["y"]}).dropna()
            neu_ics.append(float(d2["neu"].corr(d2["y"], method="spearman")))
        def summ(a):
            a = np.array(a)
            return {"ic_mean": round(float(a.mean()), 4),
                    "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if a.std(ddof=1) > 0 else 0, 2),
                    "pos_rate": round(float((a > 0).mean()) * 100, 1), "months": len(a)}
        return {"raw": summ(raw_ics), "neutral": summ(neu_ics)}

    factors = {
        "vol_20": lambda i, c, cc: -P["a_close"].iloc[i - 20:i + 1].pct_change().std().reindex(cc),
        "mom_20": lambda i, c, cc: -(c / P["a_close"].iloc[i - 20] - 1).reindex(cc),
        "mom_120": lambda i, c, cc: -(c / P["a_close"].iloc[i - 120] - 1).reindex(cc),
        "rev_5": lambda i, c, cc: -(c / P["a_close"].iloc[i - 5] - 1).reindex(cc),
    }

    def turn_factor(i, c, cc):
        tds = [d for d in tdates if d <= dates[i]]
        if len(tds) < 20:
            return None
        tm = pd.concat({d: turn[d] for d in tds[-20:]}, axis=1)
        tm = tm[~tm.index.duplicated(keep="last")].mean(axis=1)
        return -tm.reindex(cc)
    factors["turnover_20"] = turn_factor

    res = {}
    for k, fn in factors.items():
        try:
            res[k] = ic_series(fn)
        except Exception as e:  # noqa: BLE001
            res[k] = {"error": str(e)[:120]}
        print(k, res[k], flush=True)
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "note": "行业中性化效应（月频 IC）"},
           "factors": res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"neutral_effect_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 行业中性化效应 · {rep['meta']['window']}", "",
         "| 因子 | raw IC | raw t | 中性 IC | 中性 t | IC 变化 | t 变化 |", "|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        if "raw" not in v:
            L.append(f"| {k} | — | — | — | — | — | — |")
            continue
        L.append(f"| {k} | {v['raw']['ic_mean']} | {v['raw']['t']} | {v['neutral']['ic_mean']} | {v['neutral']['t']} | "
                 f"{round(v['neutral']['ic_mean'] - v['raw']['ic_mean'], 4)} | {round(v['neutral']['t'] - v['raw']['t'], 2)} |")
    L += ["", "## 读法", "- 中性化后 |t| 提升 → 该因子的收益部分来自行业暴露；",
          "- 中性化后 t 下降/消失 → 因子本身弱，收益来自行业轮动；",
          "- 若多数因子中性化后变好 → 说明行业中性应作为默认预处理。"]
    (OUT / f"neutral_effect_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: (v.get("raw", {}).get("t"), v.get("neutral", {}).get("t")) for k, v in res.items()}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
