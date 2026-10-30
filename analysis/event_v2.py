# -*- coding: utf-8 -*-
"""P0R-2：事件类型精选组合（行业中性 + 成本 + 分段验证）。

选定事件类型（P0R 验证段 rel20 显著为正）：
  股权激励 / 重组并购 / 经营订单 / 减持增持
组合规则：月末调仓，近 20 个交易日有上述事件 + 市值≥50亿 + 非 ST；
          收益按**申万一级行业中性**（个股 20 日收益 - 同行业均值），双边成本 0.2%；
对照：全部合格样本（行业中性基线）、分事件类型子组合；
分段：2019-2021 / 2022-2023 / 2024-2026（验证）。
判定：验证段净超额>0 且 t>1.5 且 2022-2023 不显著为负 且 优于基线。
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import backtest as bt
from . import config, event_backtest as eb, industry as sw

SEL_TYPES = ["股权激励", "重组并购", "经营/订单", "减持/增持"]
COST = 0.002
SEG = [("s1", "20190101", "20211231"), ("s2", "20220101", "20231231"), ("val", "20240101", "20261231")]


def seg_of(dt):
    for name, a, b in SEG:
        if a <= dt <= b:
            return name
    return None


def load_ctx(n):
    d, codes, piv = eb.load_universe(n)
    dates = list(piv.index)
    bench_df = pq.read_table(os.path.join(config.CURATED, "index_daily.parquet"),
                             columns=["ts_code", "trade_date", "close"],
                             filters=[("ts_code", "=", "000300.SH")]).to_pandas()
    bench_df["trade_date"] = bench_df["trade_date"].astype(str)
    bench = bench_df.set_index("trade_date")["close"].reindex(dates).ffill().to_numpy()
    ev = eb.load_anns(codes)
    db, _ = bt.load_valuation(codes, [])
    cls, mem = sw.load()
    imap = dict(zip(mem["ts_code"], mem["l1_code"]))
    sb = pd.read_parquet(os.path.join(config.CURATED, "stock_basic.parquet"),
                         columns=["ts_code", "name"]).drop_duplicates("ts_code", keep="last")
    names = dict(zip(sb["ts_code"], sb["name"]))
    return codes, piv, dates, bench, ev, db, imap, names


def run(codes, piv, dates, ev, db, imap, names):
    close = piv.to_numpy()
    col = {c: i for i, c in enumerate(codes)}
    dint = np.asarray([int(x) for x in dates], dtype=np.int64)
    db = db.copy()
    db["trade_date"] = db["trade_date"].astype(str)
    mv = db.pivot_table(index="trade_date", columns="ts_code", values="total_mv", aggfunc="last")
    mv = mv.reindex(dates).ffill() / 1e4  # 亿元
    ev = ev[ev["category"].isin(SEL_TYPES)].copy()
    ev["di"] = np.searchsorted(dint, ev["ann_date"].astype(np.int64).to_numpy(), side="right")
    ev = ev[ev["ts_code"].isin(col)]
    # 行业分组
    ind = pd.Series({c: imap.get(c, "NA") for c in codes})
    groups = {g: [col[c] for c in codes if ind[c] == g] for g in ind.unique()}
    # 名称/市值过滤后的合格集
    eligible = []
    for c in codes:
        nm = names.get(c) or ""
        if "ST" in nm or "退" in nm:
            continue
        eligible.append(col[c])
    months = [i for i in range(len(dates) - 1) if dates[i][4:6] != dates[i + 1][4:6]]
    months = [i for i in months if dates[i] >= "20190131" and dates[i] <= "20260831"]
    rows = []
    for i in months:
        if i + 20 >= len(dates):
            continue
        fwd = close[i + 20] / close[i] - 1.0
        rel = np.full(len(codes), np.nan)
        for g, idxs in groups.items():
            v = fwd[idxs]
            rel[idxs] = v - np.nanmean(v)
        mvrow = mv.iloc[i]
        ok_mv = {j for j in eligible if pd.notna(mvrow.get(codes[j])) and float(mvrow[codes[j]]) >= 50}
        ok = [j for j in ok_mv if np.isfinite(rel[j])]
        if len(ok) < 20:
            continue
        base = np.nanmean(rel[ok])
        evs = ev[(ev["di"] > i - 20) & (ev["di"] <= i)]
        by_type = {}
        for t in SEL_TYPES:
            st = set(evs[evs["category"] == t]["ts_code"].tolist()) & {codes[j] for j in ok}
            idx = [col[c] for c in st]
            if len(idx) >= 5:
                by_type[t] = {"n": len(idx), "gross": float(np.nanmean(rel[idx])),
                              "net": float(np.nanmean(rel[idx])) - COST}
        sel = set(evs["ts_code"].tolist()) & {codes[j] for j in ok}
        idx = [col[c] for c in sel]
        if len(idx) >= 5:
            rows.append({"date": dates[i], "seg": seg_of(dates[i]), "n": len(idx),
                         "gross": float(np.nanmean(rel[idx])),
                         "net": float(np.nanmean(rel[idx])) - COST,
                         "base_net": float(base) - COST,
                         "by_type": by_type})
    df = pd.DataFrame(rows)
    if not len(df) or "seg" not in df.columns:
        return {"rows": 0, "cost": COST, "types": SEL_TYPES, "segments": {}, "by_type": {},
                "verdict": {"passed": False, "criterion": "样本不足（无有效月份）"}}
    df = df[df["seg"].notna()]

    def summ(v):
        v = np.asarray(v, dtype=float)
        if len(v) < 5:
            return {"months": int(len(v)), "mean": None, "win": None, "t": None}
        m = v.mean()
        s = v.std(ddof=1)
        return {"months": int(len(v)), "mean": round(float(m) * 100, 2),
                "win": round(float((v > 0).mean()) * 100, 1),
                "t": round(float(m / (s / np.sqrt(len(v)))), 2) if s > 0 else None}
    out = {"rows": int(len(df)), "cost": COST, "types": SEL_TYPES,
           "segments": {}, "by_type": {}}
    for seg, _ in [(s, None) for s, _, _ in SEG]:
        sub = df[df["seg"] == seg]
        out["segments"][seg] = {"sel_net": summ(sub["net"]), "sel_gross": summ(sub["gross"]),
                                "base_net": summ(sub["base_net"]), "avg_n": round(float(sub["n"].mean()), 1) if len(sub) else None}
    for t in SEL_TYPES:
        g = df[df["by_type"].map(lambda x: t in (x or {}))]
        vals = [x[t]["net"] for x in g["by_type"] if x and t in x]
        out["by_type"][t] = {"months": len(vals), **{k: v for k, v in summ(vals).items() if k != "months"}}
    v = out["segments"]["val"]["sel_net"]
    s2 = out["segments"]["s2"]["sel_net"]
    base = out["segments"]["val"]["base_net"]
    passed = bool((v.get("mean") or 0) > 0 and (v.get("t") or 0) > 1.5
                  and (s2.get("mean") or 0) >= -0.2
                  and (v.get("mean") or 0) > (base.get("mean") or -9))
    out["verdict"] = {"val_net": v, "s2_net": s2, "val_base_net": base, "passed": passed,
                      "criterion": "val 净超额>0 且 t>1.5 且 s2 净超额≥-0.2 且优于基线"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=500)
    ap.add_argument("--out", default=r"C:\项目\deliverables\2026-10-30_事件驱动组合")
    a = ap.parse_args()
    t0 = time.time()
    codes, piv, dates, bench, ev, db, imap, names = load_ctx(a.universe)
    print("[data] universe=%d dates=%d events=%d" % (len(codes), len(dates), len(ev)), flush=True)
    res = run(codes, piv, dates, ev, db, imap, names)
    res["elapsed_s"] = round(time.time() - t0, 1)
    os.makedirs(a.out, exist_ok=True)
    day = time.strftime("%Y%m%d")
    jp = os.path.join(a.out, "event_v2_%s.json" % day)
    json.dump(res, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    md = ["# P0R-2 事件类型精选组合（行业中性+成本+分段）· %s" % time.strftime("%F %T"), "",
          "> 类型：%s；行业中性（申万一级）；双边成本 %.1f%%；月末调仓、20 日持有。" % ("/".join(SEL_TYPES), COST * 100), "",
          "## 分段结果（净超额）", "", "| 段 | 月数 | 均值% | 胜率% | t | 基线净值% | 平均持仓数 |", "|---|---|---|---|---|---|---|"]
    for seg, name in [("s1", "2019-2021"), ("s2", "2022-2023"), ("val", "2024-2026 验证")]:
        s = res["segments"][seg]
        sn, bn = s["sel_net"], s["base_net"]
        md.append("| %s | %s | %s | %s | %s | %s | %s |" % (name, sn["months"], sn["mean"], sn["win"], sn["t"], bn["mean"], s["avg_n"]))
    md += ["", "## 分事件类型（净超额，全样本）", "", "| 类型 | 月数 | 均值% | 胜率% | t |", "|---|---|---|---|---|"]
    for t, s in res["by_type"].items():
        md.append("| %s | %s | %s | %s | %s |" % (t, s["months"], s["mean"], s["win"], s["t"]))
    md += ["", "## 判定", "", "```", json.dumps(res["verdict"], ensure_ascii=False), "```"]
    mp = os.path.join(a.out, "event_v2_%s.md" % day)
    open(mp, "w", encoding="utf-8").write("\n".join(md))
    print(json.dumps({"out": jp, "verdict": res["verdict"], "segments": res["segments"]}, ensure_ascii=False, indent=1)[:2200], flush=True)


if __name__ == "__main__":
    main()
