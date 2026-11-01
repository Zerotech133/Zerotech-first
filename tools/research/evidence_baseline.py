# -*- coding: utf-8 -*-
"""证据基准重建：低波 + 反转（20/120日）+ 低换手，行业中性化，月频 topN，四关检验。

依据（H49/挖掘轮）：vol_20 t=-4.47、mom_20 t=-3.78、mom_120 t=-3.19、turnover_20 t=-2.70。
配置：
  C1 raw 复合 top20；C2 行业中性复合 top20；C3 行业中性复合 top50；
  C4 仅低波 top20；C5 仅反转复合 top20（对照，不挑格）。
成本 0.15% 单边×换手；宇宙=非ST/上市≥250日/价≥2。
输出：research/reports/evidence_baseline_<tag>.{json,md}
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
sys.path.insert(0, str(REPO / "tools" / "research"))
from sepa_backtest import load  # noqa: E402

COST = 0.0015


def zscore(s: pd.Series) -> pd.Series:
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
    csum = P["a_close"].cumsum()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    def listed_at(dt: str) -> pd.Series:
        return listed_days(sb, dt)
    # turnover（2023+）
    turn = {}
    for f in sorted(glob.glob(str(DATA / "daily_basic" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if dt >= "20190101":
            turn[dt] = pq.read_table(f, columns=["ts_code", "turnover_rate"]).to_pandas().set_index("ts_code")["turnover_rate"]
    turn_dates = sorted(turn)

    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    amt = {}
    for dt in mends:
        f = DATA / "daily" / ("d_" + dt + ".parquet")
        if f.exists():
            amt[dt] = pq.read_table(f, columns=["ts_code", "amount"]).to_pandas().set_index("ts_code")["amount"]

    def month_idx(dt):
        return dates.index(dt)

    def factor_scores(i: int, dt: str) -> pd.DataFrame:
        c = P["a_close"].iloc[i]
        tot_ret = c / P["a_close"].iloc[i - 252] - 1
        uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed_at(dt).reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
        cc = c.index[uni.fillna(False)]
        vol = -P["a_close"].iloc[i - 20:i + 1].pct_change().std().reindex(cc)          # 低波
        m20 = -(c / P["a_close"].iloc[i - 20] - 1).reindex(cc)                          # 短期反转
        m120 = -(c / P["a_close"].iloc[i - 120] - 1).reindex(cc)                        # 长期反转
        to = None
        if True:
            tds = [d for d in turn_dates if d <= dt][-20:]
            if tds:
                to = -pd.DataFrame({d: turn[d] for d in tds}).T.mean().reindex(cc)      # 低换手
        df = pd.DataFrame({"vol": zscore(vol), "m20": zscore(m20), "m120": zscore(m120),
                            "rs": tot_ret.rank(pct=True) * 98 + 1})
        if to is not None:
            df["turn"] = zscore(to)
        return df

    def mends_ts(td, dt):
        return True

    def build(neutral: bool, score_cols: list[str], topn: int) -> dict:
        prev = set()
        rets, ex, caps, regimes = [], [], [], []
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in P["a_close"].index or nxt not in P["a_close"].index:
                continue
            i = month_idx(dt)
            f = factor_scores(i, dt)
            cols = [c for c in score_cols if c in f.columns]
            if not cols:
                continue
            score = f[cols].mean(axis=1, skipna=True)
            if neutral:
                g = score.groupby(industry.reindex(score.index))
                score = (score - g.transform("mean")) / g.transform("std").replace(0, np.nan)
                score = score.dropna()
            picks = score.sort_values(ascending=False).head(topn).index
            if len(picks) < 5:
                continue
            nxt_i = month_idx(nxt)
            r = P["a_close"].iloc[nxt_i] / P["a_close"].iloc[i] - 1
            w = np.ones(len(picks)) / len(picks)
            pr = float(np.nansum(w * r.reindex(picks).fillna(0).values))
            mkt = float(r.reindex(score.index).mean())
            turnover = len(set(picks) - prev) / len(picks)
            pr -= turnover * COST * 2
            rets.append(pr); ex.append(pr - mkt)
            if dt in amt:
                caps.append(float(np.nansum(amt[dt].reindex(picks).values)) * 1000.0 * 0.05 / 1e8)
            else:
                caps.append(np.nan)
            m60 = float((P["a_close"].iloc[i] / P["a_close"].iloc[i - 60] - 1).mean()) if i >= 60 else 0.0
            regimes.append("up" if m60 > 0 else "down")
            prev = set(picks)
        rets = np.array(rets); ex = np.array(ex)
        nav = np.cumprod(1 + rets)
        yrs = len(rets) / 12
        # 年度
        yr = {}
        idx = 0
        for y in range(2019, 2027):
            seg = rets[idx: idx + 12]
            if len(seg):
                yr[str(y)] = round(float(np.prod(1 + seg) - 1) * 100, 1)
            idx += 12
        tstat = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
        pos_years = sum(1 for v in yr.values() if v > 0) / max(1, len(yr))
        ex = np.array(ex); regimes = np.array(regimes)
        up, dn = ex[regimes == "up"], ex[regimes == "down"]
        cap_med = float(np.nanmedian(caps)) if len(caps) else 0.0
        return {"cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                "vol_pct": round(float(rets.std() * np.sqrt(12)) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(tstat, 2),
                "positive_years": f"{int(pos_years*len(yr))}/{len(yr)}", "yearly": yr,
                "gates": {"G1": bool(pos_years >= 0.70 and tstat >= 2.0),
                          "G2": bool(nav[-1] > 1 and ex.mean() > 0),
                          "G3": bool(cap_med >= 0.5),
                          "G4": bool(len(up) > 6 and len(dn) > 6 and up.mean() > 0 and dn.mean() > 0)},
                "capacity_median_yi": round(cap_med, 2),
                "regime": {"up_pct": round(float(up.mean()) * 100, 3) if len(up) else None,
                            "down_pct": round(float(dn.mean()) * 100, 3) if len(dn) else None},
                "months": len(rets)}

    configs = {
        "C1_raw_top20": build(False, ["vol", "m20", "m120", "turn"], 20),
        "C2_ind_top20": build(True, ["vol", "m20", "m120", "turn"], 20),
        "C3_ind_top50": build(True, ["vol", "m20", "m120", "turn"], 50),
        "C4_vol_top20": build(True, ["vol"], 20),
        "C5_rev_top20": build(True, ["m20", "m120"], 20),
    }
    # 市场基准（日频）
    daily = P["a_close"].pct_change().mean(axis=1).fillna(0)
    mkt = (1 + daily).cumprod()
    mkt = mkt.loc[mkt.index >= mends[0]]
    yrs = len(mkt) / 252
    market = {"cagr_pct": round(float(mkt.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
              "mdd_pct": round(float((mkt / mkt.cummax() - 1).min()) * 100, 2),
              "vol_pct": round(float(daily.std() * np.sqrt(252)) * 100, 2)}
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "months": len(mends) - 1,
                    "cost": "0.15%单边×换手", "note": "证据基准重建（研究）；行业中性=行业内z；turnover 仅2023+"},
           "market": market, "configs": configs}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"evidence_baseline_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = [f"# 证据基准重建 · {rep['meta']['window']}", "",
         f"- 市场等权：CAGR {market['cagr_pct']}%、MDD {market['mdd_pct']}%、波动 {market['vol_pct']}%",
         "- 因子：低波 / 短期反转 / 长期反转 / 低换手（2023+）；行业中性=行业内 z-score；月频", "",
         "| 配置 | CAGR | MDD | 波动 | 月均超额 | t | 正收益年 | G1 |", "|---|---|---|---|---|---|---|---|"]
    for k, v in configs.items():
        g = v["gates"]
        L.append(f"| {k} | {v['cagr_pct']}% | {v['mdd_pct']}% | {v['vol_pct']}% | {v['excess_monthly_pct']}% | {v['excess_t']} | {v['positive_years']} | "
                 f"{'✅' if g['G1'] else '❌'}/{'✅' if g['G2'] else '❌'}/{'-' if g['G3'] is None else ('✅' if g['G3'] else '❌')}/{'✅' if g['G4'] else '❌'} |")
    L += ["", "## 年度收益（%）", "", "| 年 | " + " | ".join(configs) + " |", "|---|" + "---|" * len(configs)]
    years = sorted({y for v in configs.values() for y in v["yearly"]})
    for y in years:
        L.append(f"| {y} | " + " | ".join(str(configs[c]["yearly"].get(y, "")) for c in configs) + " |")
    L += ["", "## 读法", "- 看维度边际（raw vs 行业中性、top20 vs top50、单因子 vs 复合），不挑单格；",
          "- G1=正收益年≥70% 且 t≥2；仍未过则只算研究。"]
    (OUT / f"evidence_baseline_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"market": market, "configs": {k: {"cagr": v["cagr_pct"], "mdd": v["mdd_pct"],
                                                          "t": v["excess_t"], "posyears": v["positive_years"]}
                                                     for k, v in configs.items()}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
