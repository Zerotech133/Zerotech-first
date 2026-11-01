# -*- coding: utf-8 -*-
"""基本面阈值证据：净利yoy/营收yoy/ROE 是否有预测力、阈值分档是否单调。

方法（2019-2026 月频）：
  1) 每月末对全宇宙计算：netprofit_yoy、tr_yoy、roe（PIT，ann_date≤月末）
  2) 计算与下月收益的 Spearman IC（逐月）+ 分档（10 组）平均超额
  3) 阈值分档：按净利 yoy 阈值 0/10/15/20/25/30 分组，看下月平均收益是否单调抬升
输出：research/reports/fund_evidence_<tag>.{json,md}
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

THRESHOLDS = [0, 10, 15, 20, 25, 30]


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    start_i = dates.index(next(d for d in dates if d >= "20190101"))
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        """PIT：截至信号日的上市天数（2026-11-01 修复：原用最新日→次新股前视）"""
        return (pd.Timestamp(dt_) - list_dates_).dt.days
    # 财报 PIT
    rows = []
    for f in sorted(glob.glob(str(DATA / "fina_indicator" / "*.parquet"))):
        try:
            d = pq.read_table(f, columns=["ts_code", "ann_date", "netprofit_yoy", "tr_yoy", "or_yoy", "roe"]).to_pandas()
        except Exception:
            continue
        d = d[d["ann_date"].notna()]
        if not d.empty:
            rows.append(d)
    fd = pd.concat(rows, ignore_index=True)
    fd["ann_date"] = fd["ann_date"].astype(str)
    fd = fd.sort_values(["ts_code", "ann_date"])
    fund = {}
    for code, g in fd.groupby("ts_code"):
        fund[code] = (g["ann_date"].values.astype(str), g["netprofit_yoy"].values.astype(float),
                      g["tr_yoy"].values.astype(float), g["or_yoy"].values.astype(float),
                      g["roe"].values.astype(float))

    def metrics(code, date):
        a = fund.get(code)
        if not a:
            return None
        ann, npy, tryy, oryy, roe = a
        j = int(np.searchsorted(ann, date, side="right")) - 1
        if j < 0:
            return None
        rev = tryy[j] if not np.isnan(tryy[j]) else oryy[j]
        return np.array([npy[j], rev, roe[j]], dtype=float)

    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    rows_out = {"npy": [], "rev": [], "roe": []}
    ic_rows = {"npy": [], "rev": [], "roe": []}
    bucket = {t: [] for t in THRESHOLDS}
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
        data = {}
        for code in cc:
            m = metrics(code, dt)
            if m is None:
                continue
            data[code] = m
        if len(data) < 300:
            continue
        idx = list(data)
        mat = np.vstack([data[k] for k in idx])
        fw = fwd.reindex(idx).values
        for col, key in ((0, "npy"), (1, "rev"), (2, "roe")):
            from scipy.stats import spearmanr
            x = mat[:, col]
            ok = np.isfinite(x) & np.isfinite(fw)
            if ok.sum() < 200:
                continue
            ic = float(spearmanr(x[ok], fw[ok]).correlation)
            ic_rows[key].append(ic)
        # 净利阈值分档（平均下月收益）
        for t in THRESHOLDS:
            ok = np.isfinite(mat[:, 0]) & (mat[:, 0] >= t) & np.isfinite(fw)
            if ok.sum() >= 100:
                bucket[t].append(float(fw[ok].mean()))
    summary = {}
    for key in ("npy", "rev", "roe"):
        ics = np.array([x for x in ic_rows[key] if np.isfinite(x)])
        if len(ics) < 12:
            continue
        t = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else 0
        summary[key] = {"ic_mean": round(float(ics.mean()), 4), "ic_t": round(t, 2),
                        "ic_pos_rate": round(float((ics > 0).mean()) * 100, 1), "months": len(ics)}
    thr_res = {str(t): {"months": len(v), "avg_next_month_pct": round(float(np.mean(v)) * 100, 3)}
               for t, v in bucket.items() if v}
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "note": "基本面阈值证据（研究）；PIT=ann_date"},
           "factor_ic": summary, "np_yoy_threshold_buckets": thr_res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"fund_evidence_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 基本面阈值依据 · {rep['meta']['window']}", "",
         "## 因子 IC（月频 Spearman，vs 下月收益）", "",
         "| 指标 | IC 均值 | t | IC>0 比例 | 月数 |", "|---|---|---|---|---|"]
    for k, v in summary.items():
        L.append(f"| {k}（净利yoy/营收yoy/ROE） | {v['ic_mean']} | {v['ic_t']} | {v['ic_pos_rate']}% | {v['months']} |")
    L += ["", "## 净利 yoy 阈值分档（各档平均下月收益）", "", "| 阈值≥ | 月数 | 平均下月收益% |", "|---|---|---|"]
    for k, v in sorted(thr_res.items(), key=lambda x: int(x[0])):
        L.append(f"| {k}% | {v['months']} | {v['avg_next_month_pct']} |")
    L += ["", "## 读法", "- IC 显著正/t>2 → 指标方向有依据；IC≈0 → 该过滤无预测力；",
          "- 阈值分档若随阈值升高而抬升且单调 → 阈值方向正确；若平/乱 → 阈值是拍脑袋，需重新定。"]
    (OUT / f"fund_evidence_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"ic": summary, "thresholds": thr_res}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
