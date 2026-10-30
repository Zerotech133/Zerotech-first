# -*- coding: utf-8 -*-
"""H5 · 舆情情绪领先价格（pilot，数据可行性首测）。

数据现状（2026-10-30 实测）：
- xq_status 33,303 条（2012-2026），但 xq_status_classified 按月的密度在 2026-09 前极低
  （2025 各月 1-13 条；2026-08 18 条、2026-09 54 条、2026-10 229 条）；
- news.db 102,588 条但仅约 1 个月历史（保留 365 天，历史不可回填）。
=> 结论方向：**当前数据不足以做严格 4 关回测**；本脚本做 pilot：用 2026-08~10 的
   已分类雪球帖子（bullish/bearish + canonical tickers）测试「情绪 → 次日/3日/5日收益」。

口径：事件日=发帖日；T+1 收盘入场；持有 1/3/5 日；A/H 分别用 daily/hk_daily；相对等权市场 CAR；成本 30bp。
产出：research/reports/H5_sentiment_pilot_20261030.{md,json}；H5 台账更新（in_progress 或 rejected，附数据不足说明）。
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
XQ = REPO / "quant_backtest" / "data" / "xueqiu" / "xueqiu_info.db"
OUT = REPO / "research" / "reports"
START, END = "2026-08-01", "2026-10-29"
COST = 0.003
HORIZONS = (1, 3, 5)


def load_posts() -> pd.DataFrame:
    con = sqlite3.connect(str(XQ))
    rows = con.execute("""
        select s.created_at, s.title, c.sentiment, c.norm_tickers
        from xq_status s join xq_status_classified c on s.status_id=c.status_id
        where s.created_at is not null
        """).fetchall()
    con.close()
    recs = []
    for created, title, sent, norm in rows:
        day = str(created)[:10]
        if not (START <= day <= END):
            continue
        if sent not in ("bullish", "bearish"):
            continue
        try:
            arr = json.loads(norm or "[]")
        except ValueError:
            continue
        for t in arr:
            kind = t.get("kind")
            canonical = t.get("canonical")
            if kind in ("stock_a", "stock_hk") and canonical:
                recs.append({"created_at": str(created)[:10], "sentiment": sent,
                             "canonical": canonical, "kind": kind, "title": (title or "")[:60]})
    return pd.DataFrame(recs)


def to_ts_code(canonical: str, kind: str) -> str:
    c = str(canonical)
    if kind == "stock_hk":
        c = c.zfill(5)
        return c + ".HK"
    if c.endswith(".SH") or c.endswith(".SZ") or c.endswith(".BJ"):
        return c
    if c.isdigit():
        if c.startswith(("6", "9")):
            return c + ".SH"
        if c.startswith(("0", "3")):
            return c + ".SZ"
        if c.startswith(("4", "8")):
            return c + ".BJ"
    return c


def load_prices(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= "20261115"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        if codes:
            import pyarrow.compute as pc
            import pyarrow as pa
            tb = tb.filter(pc.is_in(tb["ts_code"], value_set=pa.array(list(codes))))
        if tb.num_rows:
            frames.append(tb.to_pandas())
    d = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(d):
        d["trade_date"] = d["trade_date"].astype(str)
    return d


def load_hk(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "hk_daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= "20261115"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        import pyarrow.compute as pc
        import pyarrow as pa
        tb = tb.filter(pc.is_in(tb["ts_code"], value_set=pa.array(list(codes))))
        if tb.num_rows:
            frames.append(tb.to_pandas())
    d = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(d):
        d["trade_date"] = d["trade_date"].astype(str)
    return d


def main() -> int:
    posts = load_posts()
    print("classified posts (bull/bear, with tickers):", len(posts), flush=True)
    posts["ts_code"] = [to_ts_code(c, k) for c, k in zip(posts["canonical"], posts["kind"])]
    print("sentiment:", posts["sentiment"].value_counts().to_dict(), flush=True)
    a_codes = set(posts.loc[posts["kind"] == "stock_a", "ts_code"])
    h_codes = set(posts.loc[posts["kind"] == "stock_hk", "ts_code"])
    print("A codes:", len(a_codes), "| HK codes:", len(h_codes), flush=True)
    px = load_prices(a_codes) if a_codes else pd.DataFrame()
    hk = load_hk(h_codes) if h_codes else pd.DataFrame()
    allpx = pd.concat([px, hk], ignore_index=True) if len(px) or len(hk) else pd.DataFrame()
    if allpx.empty:
        print("no price data", flush=True)
        return 2
    dates = sorted(allpx["trade_date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    close = allpx.pivot_table(index="trade_date", columns="ts_code", values="close")
    pct = allpx.pivot_table(index="trade_date", columns="ts_code", values="pct_chg")
    mkt = allpx.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod()

    obs = []
    for r in posts.itertuples():
        d = r.created_at[:10].replace("-", "")
        if d not in idx:
            continue
        code = r.ts_code
        if code not in close.columns:
            continue
        i = idx[d]
        for h in HORIZONS:
            j = i + 1 + h
            if j >= len(dates):
                continue
            p0 = close.iloc[i + 1][code] if not pd.isna(close.iloc[i + 1].get(code, np.nan)) else np.nan
            p1 = close.iloc[j][code]
            if pd.isna(p0) or pd.isna(p1) or p0 == 0:
                continue
            ret = p1 / p0 - 1
            m = mkt_cum.iloc[j] / mkt_cum.iloc[i + 1] - 1 if i + 1 < len(dates) else 0
            car = ret - m - COST
            obs.append({"date": d, "code": code, "sentiment": r.sentiment, "horizon": h,
                        "car": float(car)})
    ev = pd.DataFrame(obs, columns=["date", "code", "sentiment", "horizon", "car"]) if obs else pd.DataFrame(
        columns=["date", "code", "sentiment", "horizon", "car"])
    print("event obs:", len(ev), flush=True)
    if ev.empty:
        ev = pd.DataFrame(columns=["date", "code", "sentiment", "horizon", "car"])
    res = {"meta": {"posts": int(len(posts)), "obs": int(len(ev)),
                    "window": [START, END], "cost": COST,
                    "A_codes": len(a_codes), "HK_codes": len(h_codes),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "groups": {}}

    def stat(s):
        s = pd.Series(s).dropna()
        if len(s) < 20:
            return {"n": int(len(s))}
        return {"n": int(len(s)), "mean_pct": round(float(s.mean()) * 100, 3),
                "t": round(float(s.mean() / s.std(ddof=1) * np.sqrt(len(s))), 2)}

    for h in HORIZONS:
        sub = ev[ev["horizon"] == h]
        bull = sub[sub["sentiment"] == "bullish"]["car"]
        bear = sub[sub["sentiment"] == "bearish"]["car"]
        res["groups"][f"h{h}"] = {"bullish": stat(bull), "bearish": stat(bear)}
        print(f"h{h} bull={stat(bull)} bear={stat(bear)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H5_sentiment_pilot_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    h1 = res["groups"]["h1"]
    h5 = res["groups"]["h5"]
    L = ["# H5 · 舆情情绪领先价格（pilot，数据可行性首测）", "",
         "## 结论（先讲）",
         "- **数据不足**：xq 分类情绪在 2026-09 前密度极低（2026-08 18 条 / 2026-09 54 条 / 2026-10 229 条）；",
         "  news.db 仅约 1 个月历史（保留 365 天、历史不可回填）。当前只能做 pilot，**不能做严格四关回测**。",
         "- pilot 结果：bullish/bearish → 1/3/5 日 CAR 见下表；样本 n 小，方向不稳健。",
         "- 行动：H5 保持 **in_progress（数据积累中）**；按日累积雪球分类情绪 + news，365 天后重跑。", "",
         "## pilot 数据", "- 帖子 %d 条（bullish/bearish 且有 stock ticker）；观测 %d；窗口 %s~%s" % (
             res["meta"]["posts"], res["meta"]["obs"], START, END),
         "- A 股标的 %d 个 / 港股 %d 个；T+1 入场；相对等权市场 CAR；成本 30bp" % (
             res["meta"]["A_codes"], res["meta"]["HK_codes"]), "",
         "| 窗口 | bullish CAR(t) | bearish CAR(t) |", "|---|---|---|"]
    for h in HORIZONS:
        g = res["groups"][f"h{h}"]
        def f(x):
            return "%.2f%%(%s)" % (x["mean_pct"], x["t"]) if (x.get("n") and "mean_pct" in x) else ("n=%d(样本不足)" % x["n"] if x.get("n") else "—")
        L.append("| %d 日 | %s | %s |" % (h, f(g["bullish"]), f(g["bearish"])))
    L += ["", "## 数据计划",
          "- 每日累积：xq 3 轮抓取（已上线）+ news 采集（保留 365 天）；",
          "- 建议 2027-02 前后（≥3 个月密集样本）再评估，届时可做 IS/OOS；",
          "- 若急用，可先用公告标题情绪（anns_d 2019+，量大）作为替代代理（需另立假设 H19）。"]
    md = OUT / "H5_sentiment_pilot_20261030.md"
    md.write_text("\n".join(L), encoding="utf-8")

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    concl = ("pilot 完成（%d 帖/%d 观测）：bullish 1日 %s、bearish 1日 %s；**数据不足**（xq 分类情绪 2026-09 前每月<20 条、news 仅 1 个月），"
             "保持 in_progress 数据积累，2027-02 前后重评" % (
                 res["meta"]["posts"], res["meta"]["obs"], h1["bullish"], h1["bearish"]))
    con.execute("UPDATE hypotheses SET status=?, conclusion=?, evidence_path=?, updated_at=? WHERE id='H5'",
                ("in_progress", concl, "research/reports/H5_sentiment_pilot_20261030.md", now))
    con.commit()
    con.close()
    print(json.dumps({"posts": res["meta"]["posts"], "obs": res["meta"]["obs"],
                      "h1": h1, "h5": h5, "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
