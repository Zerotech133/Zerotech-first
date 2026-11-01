# -*- coding: utf-8 -*-
"""TT 基准条件消融：8 条逐条去掉，看哪条是「承重墙」。

组合：每月末 TT 通过 → 按 RS(ret252 百分位) top20 等权；月频；成本 0.15% 单边×换手；2019-2026。
对照：完整 TT / 去掉每条 / 空基线(仅宇宙) / 单条条件。
输出：research/reports/tt_ablation_<tag>.{json,md}
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

COST = 0.0015
TOPN = 20
CONDS = ["C1_P>S150_S200", "C2_S150>S200", "C3_S200_rising", "C4_S50>S150_S200",
         "C5_P>S50", "C6_above_low30", "C7_within_high25", "C8_RS70"]


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    start_i = dates.index(next(d for d in dates if d >= "20190101"))
    csum = P["a_close"].cumsum()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    listed = (pd.Timestamp(dates[-1]) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]

    def conditions(i: int) -> dict:
        c = P["a_close"].iloc[i]
        s50 = (csum.iloc[i] - csum.iloc[i - 50]) / 50
        s150 = (csum.iloc[i] - csum.iloc[i - 150]) / 150
        s200 = (csum.iloc[i] - csum.iloc[i - 200]) / 200
        s200p = (csum.iloc[i - 21] - csum.iloc[i - 221]) / 200
        win = P["a_close"].iloc[i - 249: i + 1]
        hi, lo = win.max(), win.min()
        r252 = c / P["a_close"].iloc[i - 252] - 1
        rs = r252.rank(pct=True) * 98 + 1
        uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
        conds = {
            "C1_P>S150_S200": (c > s150) & (c > s200),
            "C2_S150>S200": s150 > s200,
            "C3_S200_rising": s200 > s200p,
            "C4_S50>S150_S200": (s50 > s150) & (s50 > s200),
            "C5_P>S50": c > s50,
            "C6_above_low30": c >= 1.30 * lo,
            "C7_within_high25": c >= 0.75 * hi,
            "C8_RS70": rs >= 70,
        }
        return {"c": c, "rs": rs, "uni": uni.fillna(False), "conds": {k: v.fillna(False) for k, v in conds.items()}}

    def run(mode: str, drop: str = "") -> dict:
        prev = set()
        rets, ex = [], []
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in P["a_close"].index or nxt not in P["a_close"].index:
                continue
            i = dates.index(dt)
            x = conditions(i)
            base = x["uni"]
            if mode == "full":
                for k in CONDS:
                    base &= x["conds"][k]
            elif mode == "drop":
                for k in CONDS:
                    if k != drop:
                        base &= x["conds"][k]
            elif mode == "single":
                base &= x["conds"][drop]
            cc = x["c"].index[base]
            if len(cc) < 5:
                rets.append(0.0)
                ex.append(0.0)
                continue
            picks = x["rs"].reindex(cc).sort_values(ascending=False).head(TOPN).index
            nxt_i = dates.index(nxt)
            r = P["a_close"].iloc[nxt_i] / x["c"] - 1
            pr = float(r.reindex(picks).mean())
            mkt = float(r.reindex(cc).mean())
            turnover = len(set(picks) - prev) / len(picks)
            pr -= turnover * COST * 2
            rets.append(pr); ex.append(pr - mkt)
            prev = set(picks)
        rets = np.array(rets); ex = np.array(ex)
        nav = np.cumprod(1 + rets)
        yrs = len(rets) / 12
        return {"mode": mode, "drop": drop, "cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3),
                "excess_t": round(float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0, 2),
                "months": len(rets)}

    results = [run("full"), run("empty")]
    for k in CONDS:
        results.append(run("drop", k))
    for k in CONDS:
        results.append(run("single", k))
    tag = datetime.now().strftime("%Y%m%d")
    full = results[0]
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "top": TOPN, "cost": "0.15%单边×换手",
                    "note": "TT 条件消融（研究）；RS=ret252 百分位；月频"},
           "full": full, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"tt_ablation_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# TT 基准条件消融 · {rep['meta']['window']}", "",
             f"- 完整 TT：CAGR {full['cagr_pct']}%、MDD {full['mdd_pct']}%、月均超额 {full['excess_monthly_pct']}%（t={full['excess_t']}）", "",
             "## 去掉单条（Ablation）：CAGR 变化 = 该条件的贡献", "",
             "| 去掉 | CAGR | ΔCAGR(pp) | MDD | 月均超额 | t |", "|---|---|---|---|---|---|"]
    for r in results[2:10]:
        lines.append(f"| {r['drop']} | {r['cagr_pct']}% | {round(r['cagr_pct'] - full['cagr_pct'], 2)} | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} |")
    lines += ["", "## 单条条件单独使用（vs 空基线）", "", "| 条件 | CAGR | MDD | 月均超额 | t |", "|---|---|---|---|---|"]
    for r in results[10:]:
        lines.append(f"| {r['drop']} | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} |")
    lines += ["", "## 读法", "- 去掉某条 CAGR 明显下降（Δ<0）→ 该条是承重条件；",
              "- 去掉后 CAGR 不变/上升 → 该条可能是冗余或有害；",
              "- 单条条件单独使用若明显优于空基线 → 该条本身有筛选价值。"]
    (OUT / f"tt_ablation_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"full": full, "drop": [(r["drop"], round(r["cagr_pct"] - full["cagr_pct"], 2)) for r in results[2:10]],
                      "single": [(r["drop"], r["cagr_pct"]) for r in results[10:]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
