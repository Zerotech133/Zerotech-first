# -*- coding: utf-8 -*-
"""H21 · 负面公告 × 主力净流出 复合规避信号。

假设：负面公告（标题词表）× 事件前 5 日主力净流出（低分位）→ 未来 5/10/20 日 CAR 更差
（「负面+流出」组显著弱于「负面+流入」组），可作为规避规则。

数据：anns_d（2023-2026 标题）、moneyflow（2023-01 起）、daily。
样本：2023-01 ~ 2026-10-29；IS ≤2023-12-31 / OOS ≥2024-01-01；成本 30bp；T+1 收盘入场。
产出：research/reports/H21_negative_ann_moneyflow_20261030.{md,json}；台账 H21。
"""
from __future__ import annotations

import glob
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20230101", "20261029"
IS_END = "20231231"
COST = 0.003

NEG = re.compile(r"预减|预亏|减持|立案|处罚|亏损|退市|违约|诉讼|下调|业绩预减|首亏|监管|问询|警示|冻结|终止|失败|风险提示")
POS = re.compile(r"预增|扭亏|增持|回购|中标|获批|分红|超预期|上调|增长|签约|签订|合作|收购|盈利|扭亏为盈")


def load_events() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA / "anns_d" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ann_date", "ts_code", "title"])
        anns = tb.column("ann_date").to_pylist()
        codes = tb.column("ts_code").to_pylist()
        titles = tb.column("title").to_pylist()
        for ann, code, title in zip(anns, codes, titles):
            t = str(title or "")
            if NEG.search(t) and not POS.search(t):
                rows.append({"ann_date": str(ann)[:8], "ts_code": str(code)})
    ev = pd.DataFrame(rows).drop_duplicates()
    return ev[ev["ts_code"].str.match(r"^\d{6}\.(SH|SZ)$")]


def load_daily(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= "20261115"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        df = tb.to_pandas()
        df = df[df["ts_code"].isin(codes)]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    d["trade_date"] = d["trade_date"].astype(str)
    return d


def load_money(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "moneyflow" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "buy_lg_amount", "sell_lg_amount",
                                       "buy_elg_amount", "sell_elg_amount"])
        df = tb.to_pandas()
        df = df[df["ts_code"].isin(codes)]
        if len(df):
            frames.append(df)
    m = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    m["trade_date"] = m["trade_date"].astype(str)
    m["net_lg"] = (m["buy_lg_amount"] + m["buy_elg_amount"]) - (m["sell_lg_amount"] + m["sell_elg_amount"])
    m["gross"] = m["buy_lg_amount"] + m["sell_lg_amount"] + m["buy_elg_amount"] + m["sell_elg_amount"]
    return m


def main() -> int:
    ev = load_events()
    print("negative events:", len(ev), "codes:", ev["ts_code"].nunique(), flush=True)
    codes = set(ev["ts_code"])
    px = load_daily(codes)
    print("daily rows:", len(px), flush=True)
    mf = load_money(codes)
    print("moneyflow rows:", len(mf), flush=True)
    mf = mf.sort_values(["ts_code", "trade_date"])
    g = mf.groupby("ts_code")
    mf["pre5_net"] = g["net_lg"].transform(lambda s: s.rolling(5, min_periods=3).sum())
    mf["pre5_gross"] = g["gross"].transform(lambda s: s.rolling(5, min_periods=3).sum())
    mf["pre5_ratio"] = mf["pre5_net"] / mf["pre5_gross"].replace(0, np.nan)
    ev2 = ev.merge(mf[["ts_code", "trade_date", "pre5_ratio"]], left_on=["ts_code", "ann_date"],
                   right_on=["ts_code", "trade_date"], how="inner")
    print("events with moneyflow:", len(ev2), flush=True)

    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod()
    dates = list(close.index)
    idx = {d: i for i, d in enumerate(dates)}

    obs = []
    for r in ev2.itertuples():
        d = r.ann_date
        if d not in idx:
            continue
        i = idx[d] + 1  # T+1 收盘入场
        if i >= len(dates):
            continue
        code = r.ts_code
        if code not in close.columns:
            continue
        p0 = close.iloc[i].get(code, np.nan)
        if pd.isna(p0) or p0 == 0:
            continue
        for h in (5, 10, 20):
            j = i + h
            if j >= len(dates):
                continue
            p1 = close.iloc[j].get(code, np.nan)
            if pd.isna(p1):
                continue
            ret = p1 / p0 - 1
            m = mkt_cum.iloc[j] / mkt_cum.iloc[i] - 1
            car = ret - m - COST
            if abs(car) > 0.8:
                continue
            obs.append({"date": d, "code": code, "car": float(car), "h": h,
                        "ratio": float(r.pre5_ratio), "split": "IS" if d <= IS_END else "OOS"})
    e = pd.DataFrame(obs)
    print("observations:", len(e), flush=True)
    # 每日横截面分位：高流出(rank<=0.33) / 高流入(>=0.67)
    e["rk"] = e.groupby("date")["ratio"].rank(pct=True)
    e["grp"] = np.where(e["rk"] <= 0.33, "outflow", np.where(e["rk"] >= 0.67, "inflow", "mid"))

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 50:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    res = {"meta": {"events": int(len(ev)), "events_with_mf": int(len(ev2)), "obs": int(len(e)),
                    "start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "groups": {}}
    for h in (5, 10, 20):
        sub = e[e["h"] == h]
        gout = sub[sub["grp"] == "outflow"]
        gin = sub[sub["grp"] == "inflow"]
        diff = gout["car"].groupby(gout["date"]).mean() - gin["car"].groupby(gin["date"]).mean()
        res["groups"][f"h{h}"] = {
            "outflow": {"all": stat(gout["car"]), "IS": stat(gout[gout["split"] == "IS"]["car"]),
                        "OOS": stat(gout[gout["split"] == "OOS"]["car"])},
            "inflow": {"all": stat(gin["car"]), "IS": stat(gin[gin["split"] == "IS"]["car"]),
                       "OOS": stat(gin[gin["split"] == "OOS"]["car"])},
            "diff": {"all": stat(diff),
                     "IS": stat(diff[diff.index <= IS_END]),
                     "OOS": stat(diff[diff.index > IS_END])},
        }
        print(f"h{h}: outflow={res['groups'][f'h{h}']['outflow']['all']} inflow={res['groups'][f'h{h}']['inflow']['all']} diff_OOS={res['groups'][f'h{h}']['diff']['OOS']}", flush=True)

    d10 = res["groups"]["h10"]["diff"]["OOS"]
    status = "backtested" if (d10.get("n", 0) >= 50 and d10.get("t") is not None
                              and d10["t"] <= -2) else "rejected"
    res["status"] = status

    js = OUT / "H21_negative_ann_moneyflow_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"])

    L = ["# H21 · 负面公告 × 主力净流出（复合规避信号）", "",
         "- 样本：负面公告事件 **%d**（2023-01~2026-10），有 moneyflow 的 %d；观测 %d；IS≤%s / OOS≥2024-01；成本 30bp" % (
             res["meta"]["events"], res["meta"]["events_with_mf"], res["meta"]["obs"], IS_END),
         "- 分组：事件前 5 日主力净流入比（net_lg/gross）当日横截面分位：流出组≤0.33 / 流入组≥0.67", "",
         "| 窗口 | 流出组 CAR | 流入组 CAR | 差（流出-流入） |", "|---|---|---|---|"]
    for h in (5, 10, 20):
        g = res["groups"][f"h{h}"]
        L.append("| %d 日 | %s | %s | %s |" % (h, f(g["outflow"]["all"]), f(g["inflow"]["all"]), f(g["diff"]["all"])))
    L += ["", "## 分样本（10 日）", "| 样本 | 流出组 | 流入组 | 差 |", "|---|---|---|---|"]
    for s in ("IS", "OOS"):
        g = res["groups"]["h10"]
        L.append("| %s | %s | %s | %s |" % (s, f(g["outflow"][s]), f(g["inflow"][s]), f(g["diff"][s])))
    L += ["", "## 结论", "- 状态：**%s**（10 日差 OOS：%s）" % (status, f(d10)), "",
          "## 局限", "- moneyflow 仅 2023-01 起（IS 仅 1 年）；CAR 相对等权市场；",
          "- 事件重叠（同股多次公告）、未剔除涨跌停/停牌不可成交；标题词表非语义模型。"]
    (OUT / "H21_negative_ann_moneyflow_20261030.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"status": status, "diff10_oos": d10, "md": str(OUT / "H21_negative_ann_moneyflow_20261030.md")},
                     ensure_ascii=False, indent=1), flush=True)

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    exists = con.execute("SELECT id FROM hypotheses WHERE id='H21'").fetchone()
    row = {"id": "H21", "title": "负面公告 × 主力净流出（复合规避）",
           "question": "负面公告叠加事件前主力净流出，是否显著弱于净流入组（可作规避规则）？",
           "data_sources": "anns_d + moneyflow + daily",
           "method": "负面标题事件；前5日主力净流入比横截面分位；5/10/20日 CAR；IS≤2023/OOS≥2024；30bp",
           "gates": "PIT=是（事件日已知）；OOS=独立；成本=30bp；容量=事件数充足",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "事件 %d / 观测 %d；10日 流出 vs 流入差 OOS %s → %s" % (
               res["meta"]["events"], res["meta"]["obs"], f(d10), status),
           "evidence_path": "research/reports/H21_negative_ann_moneyflow_20261030.md",
           "created_at": now, "updated_at": now}
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    keys = [k for k in row if k in cols]
    if exists:
        con.execute("UPDATE hypotheses SET %s WHERE id='H21'" % ",".join("%s=?" % k for k in keys if k != "id"),
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print("H21 已写台账", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
