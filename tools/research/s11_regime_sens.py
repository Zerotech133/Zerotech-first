# -*- coding: utf-8 -*-
"""S11 regime 阈值敏感性：市场趋势阈值/回看窗口是否刀锋。

配置固定：K20、-ret120、分数加权、月频、PIT 宇宙。
regime 定义：市场等权指数过去 N 日收益 < 阈值 → 允许持仓（否则空仓）。
阈值网格：-8/-5/-2/0/+2/+5%；N=40/60/120；另加「无过滤」基准。
输出：research/reports/s11_regime_sens_<tag>.{json,md}
判定：若仅 0% 附近有效/相邻阈值大幅劣化 → 过拟合；若相邻阈值 t 均≥1.5 且 MDD 改善稳定 → 较稳健。
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
K = 20


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    list_dates = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    amt = {}
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    for dt in mends:
        f = DATA / "daily" / ("d_" + dt + ".parquet")
        if f.exists():
            amt[dt] = pq.read_table(f, columns=["ts_code", "amount"]).to_pandas().set_index("ts_code")["amount"]
    mret = P["a_close"].pct_change().mean(axis=1)
    mkt = (1 + mret).cumprod()

    def run(lookback: int, threshold: float | None) -> dict:
        prev = set(); rets, mkts, caps, active = [], [], [], 0
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in P["a_close"].index or nxt not in P["a_close"].index:
                continue
            i = dates.index(dt)
            if threshold is not None:
                if i < lookback + 1:
                    rets.append(0.0); mkts.append(0.0); caps.append(np.nan); continue
                mtr = float(mkt.iloc[i] / mkt.iloc[i - lookback] - 1)
                if not (mtr < threshold):
                    rets.append(0.0); mkts.append(0.0); caps.append(np.nan); continue
            active += 1
            c = P["a_close"].iloc[i]
            uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                  ((pd.Timestamp(dt) - list_dates).dt.days.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna() & \
                  ~c.index.str.endswith(".BJ")
            cc = c.index[uni.fillna(False)]
            score = (-(c / P["a_close"].iloc[i - 120] - 1)).reindex(cc).dropna()
            picks = score.sort_values(ascending=False).head(K).index
            if len(picks) < 5:
                continue
            r = np.arange(len(picks), 0, -1, dtype=float); w = r / r.sum()
            fwd = P["a_close"].iloc[dates.index(nxt)] / c - 1
            pr = float(np.nansum(w * fwd.reindex(picks).fillna(0).values))
            mkt_r = float(fwd.reindex(cc).mean())
            turnover = len(set(picks) - prev) / len(picks)
            pr -= turnover * COST * 2
            rets.append(pr); mkts.append(mkt_r)
            caps.append(float(np.nansum(amt[dt].reindex(picks).values)) * 1000 * 0.05 / 1e8 if dt in amt else np.nan)
            prev = set(picks)
        rets = np.array(rets); mkts = np.array(mkts)
        nav = np.cumprod(1 + rets); yrs = len(rets) / 12
        ex = rets - mkts
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
        return {"lookback": lookback, "threshold_pct": threshold, "active_months": active,
                "active_ratio_pct": round(active / len(rets) * 100, 1),
                "cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2),
                "capacity_median_yi": round(float(np.nanmedian(caps)), 2)}

    results = [run(60, None)]
    for lb in (40, 60, 120):
        for th in (-0.08, -0.05, -0.02, 0.0, 0.02, 0.05):
            results.append(run(lb, th))
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "config": "K20/score/月频/-ret120/PIT",
                    "note": "regime 阈值敏感性；CAGR 含空仓月（0 收益）"},
           "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"s11_regime_sens_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = [f"# S11 regime 阈值敏感性 · {rep['meta']['window']}", "",
         "> 固定：K20/分数加权/月频/-ret120；regime=市场等权过去 N 日收益 < 阈值才持仓；空仓月按 0 收益计入", "",
         "| 回看 N | 阈值 | 活跃月占比 | CAGR | MDD | 月均超额 | t | 容量 |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        th = "无过滤" if r["threshold_pct"] is None else f"{r['threshold_pct']*100:+.0f}%"
        L.append(f"| {r['lookback']} | {th} | {r['active_ratio_pct']}% | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} | {r['capacity_median_yi']}亿 |")
    # 判定
    valid = [r for r in results if r["threshold_pct"] is not None]
    tvals = [r["excess_t"] for r in valid]
    robust = sum(1 for x in tvals if x >= 1.5) / len(tvals)
    L += ["", "## 判定", f"- 18 个阈值组合中 t≥1.5 的比例：**{robust*100:.0f}%**；t 范围 {min(tvals):.2f} ~ {max(tvals):.2f}",
          f"- 无过滤基准：t={results[0]['excess_t']}、MDD={results[0]['mdd_pct']}%",
          "- 若只有 0% 附近好、相邻阈值显著劣化 → 刀锋/过拟合；若 -5%~+5% 均 t≥1.5 → 较稳健。"]
    (OUT / f"s11_regime_sens_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"no_filter": results[0], "t_values": [(f"{r['lookback']}/{r['threshold_pct']}", r["excess_t"], r["mdd_pct"]) for r in valid]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
