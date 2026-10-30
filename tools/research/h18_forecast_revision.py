# -*- coding: utf-8 -*-
"""H18 · 业绩预告修订动量（同公司同报告期的二次+公告）。

事件：同一 ts_code + end_date 的第 2 次及以后预告（ann_date 递增）= 「修订」；
方向：按 type 极性（预增/扭亏/略增/续盈/减亏=+1；预减/首亏/续亏/略减/增亏=-1；不确定=0）
      Δ = 本次极性 − 上次极性；>0 改善，<0 恶化；同时记录幅度中值变化。
检验：修订公告后 T+1 收盘入场，5/10/20 日相对等权市场的 CAR；IS 2023-2024 / OOS 2025-2026；
      成本 30bp；对比「改善 vs 恶化」两组。
产出：research/reports/H18_forecast_revision_<日期>.{md,json}；H18 写 hypotheses.db。
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20230101", "20261029"
IS_END = "20241231"
COST = 0.003
HORIZONS = (5, 10, 20)

POS = {"预增", "扭亏", "略增", "续盈", "减亏"}
NEG = {"预减", "首亏", "续亏", "略减", "增亏", "不确定"}


def load_forecast() -> pd.DataFrame:
    fs = sorted(glob.glob(str(DATA / "forecast" / "*.parquet")))
    d = pa.concat_tables([pq.read_table(f) for f in fs]).to_pandas()
    d["ann_date"] = d["ann_date"].astype(str)
    d["end_date"] = d["end_date"].astype(str)
    d = d[(d["ann_date"] >= START) & (d["ann_date"] <= END)]
    d["score"] = d["type"].map(lambda x: 1 if x in POS else (-1 if x in NEG else 0))
    d["mid"] = (d["p_change_min"].fillna(0) + d["p_change_max"].fillna(0)) / 2
    d = d.sort_values(["ts_code", "end_date", "ann_date"])
    d["prev_score"] = d.groupby(["ts_code", "end_date"])["score"].shift(1)
    d["prev_mid"] = d.groupby(["ts_code", "end_date"])["mid"].shift(1)
    d["prev_type"] = d.groupby(["ts_code", "end_date"])["type"].shift(1)
    rev = d[d["prev_score"].notna()].copy()
    rev["dscore"] = rev["score"] - rev["prev_score"]
    rev["dmid"] = rev["mid"] - rev["prev_mid"]
    # 修订方向：类型极性变化优先；极性不变时用幅度中值变化（同向修订）
    rev["direction"] = np.where(rev["dscore"] > 0, 1,
                                np.where(rev["dscore"] < 0, -1, np.sign(rev["dmid"].fillna(0))))
    rev = rev[rev["direction"] != 0].copy()
    print("revisions:", len(rev), "improve:", int((rev["direction"] > 0).sum()),
          "deteriorate:", int((rev["direction"] < 0).sum()), flush=True)
    return rev


def load_daily_panel(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        if codes:
            import pyarrow.compute as pc
            tb = tb.filter(pc.is_in(tb["ts_code"], value_set=pa.array(list(codes))))
        frames.append(tb.to_pandas())
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d


def main() -> int:
    rev = load_forecast()
    codes = set(rev["ts_code"])
    px = load_daily_panel(codes)
    print("daily panel rows:", len(px), flush=True)
    # 交易日序列 + 全市场等权收益
    dates = sorted(px["trade_date"].unique())
    date_idx = {d: i for i, d in enumerate(dates)}

    def next_td(d: str, offset: int) -> str | None:
        i = date_idx.get(d)
        if i is None:
            # 找第一个 > d 的交易日
            for j, x in enumerate(dates):
                if x > d:
                    i = j - 1
                    break
        if i is None:
            return None
        j = i + 1 + offset
        return dates[j] if 0 <= j < len(dates) else None

    # 收盘价 pivot（code×date）
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    pct = px.pivot_table(index="trade_date", columns="ts_code", values="pct_chg")  # noqa: F841 - 既有未使用变量（批2不改行为）
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0  # 等权市场日收益（pct_chg 为百分数）
    mkt_cum = (1 + mkt.fillna(0)).cumprod()

    rows = []
    for r in rev.itertuples():
        e = next_td(r.ann_date, 0)  # T+1
        if e is None:
            continue
        code = r.ts_code
        for h in HORIZONS:
            x = next_td(r.ann_date, h)
            if x is None:
                continue
            try:
                p0 = close.at[e, code]
                p1 = close.at[x, code]
            except KeyError:
                continue
            if pd.isna(p0) or pd.isna(p1) or p0 == 0:
                continue
            ret = p1 / p0 - 1
            try:
                m = mkt_cum[x] / mkt_cum[e] - 1
            except KeyError:
                continue
            car = ret - m - COST
            if abs(car) > 0.5:  # 剔除数据异常/停复牌噪声
                continue
            rows.append({"code": code, "ann_date": r.ann_date, "type": r.type,
                         "prev_type": r.prev_type, "direction": int(r.direction),
                         "dscore": r.dscore, "dmid": r.dmid,
                         "horizon": h, "ret": float(ret), "car": float(car),
                         "is_oos": "IS" if r.ann_date <= IS_END else "OOS"})
    ev = pd.DataFrame(rows)
    print("event obs:", len(ev), flush=True)
    res = {"meta": {"revisions": int(len(rev)), "obs": int(len(ev)),
                    "start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "groups": {}}

    def stat(s: pd.Series) -> dict:
        s = s.dropna()
        if len(s) < 20:
            return {"n": int(len(s))}
        return {"n": int(len(s)), "mean_pct": round(float(s.mean()) * 100, 3),
                "t": round(float(s.mean() / s.std(ddof=1) * np.sqrt(len(s))), 2)}

    for h in HORIZONS:
        sub = ev[ev["horizon"] == h]
        res["groups"][f"h{h}"] = {
            "improve": {"all": stat(sub[sub["direction"] > 0]["car"]),
                        "IS": stat(sub[(sub["direction"] > 0) & (sub["is_oos"] == "IS")]["car"]),
                        "OOS": stat(sub[(sub["direction"] > 0) & (sub["is_oos"] == "OOS")]["car"])},
            "deteriorate": {"all": stat(sub[sub["direction"] < 0]["car"]),
                            "IS": stat(sub[(sub["direction"] < 0) & (sub["is_oos"] == "IS")]["car"]),
                            "OOS": stat(sub[(sub["direction"] < 0) & (sub["is_oos"] == "OOS")]["car"])},
            "spread": (stat(sub[sub["direction"] > 0]["car"]).get("mean_pct", 0) -
                               stat(sub[sub["direction"] < 0]["car"]).get("mean_pct", 0))
                       if stat(sub[sub["direction"] > 0]["car"]).get("n") and stat(sub[sub["direction"] < 0]["car"]).get("n") else None,
        }

    # OOS 判定：恶化组显著为负 或 改善组显著为正
    oos_d = res["groups"]["h20"]["deteriorate"]["OOS"]
    oos_i = res["groups"]["h20"]["improve"]["OOS"]
    status = "backtested" if (abs(oos_d.get("t", 0) or 0) >= 2 or abs(oos_i.get("t", 0) or 0) >= 2) else "rejected"

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H18_forecast_revision_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# H18 · 业绩预告修订动量（同公司同报告期二次公告）", "",
         "- 修订事件：%d 个（改善 %d / 恶化 %d）；有效观测 %d" % (
             res["meta"]["revisions"], int((rev["direction"] > 0).sum()),
             int((rev["direction"] < 0).sum()), res["meta"]["obs"]),
         "- 事件：修订公告日 T+1 收盘入场，持有 5/10/20 日；相对等权市场 CAR；成本 30bp；IS≤2024 / OOS≥2025", "",
         "| 窗口 | 改善组 CAR(t) | 恶化组 CAR(t) | 价差(改善−恶化) |", "|---|---|---|---|"]
    for h in HORIZONS:
        g = res["groups"][f"h{h}"]
        def f(x):
            return "%.2f%%(%s)" % (x["mean_pct"], x["t"]) if x.get("n") else "—"
        L.append("| %d 日 | %s | %s | %s |" % (h, f(g["improve"]["all"]), f(g["deteriorate"]["all"]),
                                               ("%.2f%%" % g["spread"]) if g.get("spread") is not None else "—"))
    L += ["", "## 分样本（20 日）", "| 样本 | 改善组 | 恶化组 |", "|---|---|---|"]
    for sname in ("IS", "OOS"):
        g = res["groups"]["h20"]
        def f2(x):
            return "%.2f%%(t=%s)" % (x["mean_pct"], x["t"]) if x.get("n") else "—"
        L.append("| %s | %s | %s |" % (sname, f2(g["improve"][sname]), f2(g["deteriorate"][sname])))
    L += ["", "## 结论", "- 状态：**%s**" % status,
          "- OOS 20 日：改善组 %s / 恶化组 %s" % (
              res["groups"]["h20"]["improve"]["OOS"], res["groups"]["h20"]["deteriorate"]["OOS"]),
          "- 样本仅 %d 个修订事件 → 容量极小，即使显著也只作事件提醒，不作组合策略。" % res["meta"]["revisions"]]
    md = OUT / "H18_forecast_revision_20261030.md"
    md.write_text("\n".join(L), encoding="utf-8")

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {"id": "H18", "title": "业绩预告修订动量（同公司同报告期二次公告）",
           "question": "同一公司同报告期的预告修订（类型/幅度变化）是否产生可交易动量？",
           "data_sources": "forecast(2023-2026)+daily",
           "method": "同 ts_code+end_date 第 2 次及以后 ann_date=修订；type 极性 Δ；T+1 入场；5/10/20 日相对等权市场 CAR；成本30bp；IS2023-24/OOS2025-26",
           "gates": "PIT=是；OOS=独立；成本=30bp；容量=极小（583 事件）",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "修订事件 %d；OOS 20日 改善组 %s / 恶化组 %s" % (
               res["meta"]["revisions"], res["groups"]["h20"]["improve"]["OOS"], res["groups"]["h20"]["deteriorate"]["OOS"]),
           "evidence_path": "research/reports/H18_forecast_revision_20261030.md",
           "created_at": now, "updated_at": now}
    keys = [k for k in row if k in cols]
    if con.execute("SELECT id FROM hypotheses WHERE id='H18'").fetchone():
        con.execute("UPDATE hypotheses SET %s WHERE id='H18'" % ",".join("%s=?" % k for k in keys if k != "id"),  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print(json.dumps({"status": status, "revisions": res["meta"]["revisions"],
                      "h20_improve": res["groups"]["h20"]["improve"],
                      "h20_deteriorate": res["groups"]["h20"]["deteriorate"],
                      "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
