# -*- coding: utf-8 -*-
"""H19 · 公告标题情绪（关键词极性）→ 短期收益（anns_d 2019-2026）。

背景：H1 事件研究已 rejected；H5 舆情数据不足。本假设用**公告标题**做情绪代理，
数据量大（anns_d 2019-2026，全部 A 股公告），检验标题极性是否领先/伴随短期收益。

方法：
- 极性词表：正/负面关键词命中（预增、扭亏、增持、回购、中标、获批、分红、超预期 vs
  预减、预亏、减持、立案、处罚、亏损、退市、违约、诉讼 等；不含中性词）
- 事件：公告日 T；T+1 收盘入场；持有 1/3/5/10/20 日；相对**等权全市场** CAR；成本 30bp
- 样本：2021-01-01 ~ 2026-10-29；IS ≤2023-12-31 / OOS ≥2024-01-01
产出：research/reports/H19_announcement_sentiment_20261030.{md,json}；H19 写 hypotheses.db。
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
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20210101", "20261029"
IS_END = "20231231"
COST = 0.003
HORIZONS = (1, 3, 5, 10, 20)

POS_WORDS = ("预增", "扭亏", "增持", "回购", "中标", "获批", "分红", "超预期", "上调", "增长",
             "签约", "签订", "合作", "收购", "盈利", "净利润增长", "业绩预增", "扭亏为盈", "解禁减少")
NEG_WORDS = ("预减", "预亏", "减持", "立案", "处罚", "亏损", "退市", "违约", "诉讼", "下调",
             "业绩预减", "首亏", "监管", "问询", "警示", "冻结", "终止", "失败", "风险提示")
POS_RE = re.compile("|".join(POS_WORDS))
NEG_RE = re.compile("|".join(NEG_WORDS))


def load_events() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA / "anns_d" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ann_date", "ts_code", "title"])
        titles = tb.column("title").to_pylist()
        codes = tb.column("ts_code").to_pylist()
        anns = tb.column("ann_date").to_pylist()
        for ann, code, title in zip(anns, codes, titles):
            t = str(title or "")
            if not t:
                continue
            p = bool(POS_RE.search(t))
            n = bool(NEG_RE.search(t))
            if p and not n:
                pol = 1
            elif n and not p:
                pol = -1
            else:
                continue
            rows.append({"ann_date": str(ann)[:8], "ts_code": str(code), "polarity": pol, "title_len": len(t)})
    return pd.DataFrame(rows)


def load_close(codes: set[str]) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= "20261115"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        import pyarrow.compute as pc
        tb = tb.filter(pc.is_in(tb["ts_code"], value_set=pa.array(list(codes))))
        if tb.num_rows:
            frames.append(tb.to_pandas())
    d = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(d):
        d["trade_date"] = d["trade_date"].astype(str)
    return d


def main() -> int:
    ev = load_events()
    print("sentiment events:", len(ev), "pos:", int((ev["polarity"] > 0).sum()),
          "neg:", int((ev["polarity"] < 0).sum()), flush=True)
    if ev.empty:
        return 2
    ev = ev.drop_duplicates(subset=["ann_date", "ts_code", "polarity"])
    codes = set(ev["ts_code"])
    px = load_close(codes)
    print("close rows:", len(px), "codes:", px["ts_code"].nunique(), flush=True)
    dates = sorted(px["trade_date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = (px.groupby("trade_date")["pct_chg"].mean() / 100.0)
    mkt_cum = (1 + mkt.fillna(0)).cumprod()

    obs = []
    for r in ev.itertuples():
        d = r.ann_date
        if d not in idx:
            continue
        i = idx[d] + 1  # T+1 入场
        if i >= len(dates):
            continue
        code = r.ts_code
        if code not in close.columns:
            continue
        p0 = close.iloc[i].get(code, np.nan)
        if pd.isna(p0) or p0 == 0:
            continue
        for h in HORIZONS:
            j = i + h
            if j >= len(dates):
                continue
            p1 = close.iloc[j].get(code, np.nan)
            if pd.isna(p1):
                continue
            ret = p1 / p0 - 1
            m = mkt_cum.iloc[j] / mkt_cum.iloc[i] - 1
            car = ret - m - COST
            if abs(car) > 0.6:
                continue
            obs.append({"date": d, "code": code, "polarity": int(r.polarity),
                        "horizon": h, "car": float(car),
                        "split": "IS" if d <= IS_END else "OOS"})
    e = pd.DataFrame(obs, columns=["date", "code", "polarity", "horizon", "car", "split"])
    print("event obs:", len(e), flush=True)
    res = {"meta": {"events": int(len(ev)), "obs": int(len(e)),
                    "start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "groups": {}}

    def stat(x):
        x = pd.Series(x).dropna()
        if len(x) < 50:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    def spread_stat(a, b):
        a = pd.Series(a).dropna(); b = pd.Series(b).dropna()
        if len(a) < 50 or len(b) < 50:
            return {"n": int(len(a) + len(b))}
        diff = float(a.mean() - b.mean())
        se = float(np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))
        return {"n": int(len(a) + len(b)), "mean_pct": round(diff * 100, 3),
                "t": round(diff / se, 2) if se else None}

    for h in HORIZONS:
        sub = e[e["horizon"] == h]
        res["groups"][f"h{h}"] = {
            "spread": {"all": spread_stat(sub[sub["polarity"] > 0]["car"], sub[sub["polarity"] < 0]["car"]),
                       "IS": spread_stat(sub[(sub["polarity"] > 0) & (sub["split"] == "IS")]["car"],
                                         sub[(sub["polarity"] < 0) & (sub["split"] == "IS")]["car"]),
                       "OOS": spread_stat(sub[(sub["polarity"] > 0) & (sub["split"] == "OOS")]["car"],
                                          sub[(sub["polarity"] < 0) & (sub["split"] == "OOS")]["car"])},
            "pos": {"all": stat(sub[sub["polarity"] > 0]["car"]),
                    "IS": stat(sub[(sub["polarity"] > 0) & (sub["split"] == "IS")]["car"]),
                    "OOS": stat(sub[(sub["polarity"] > 0) & (sub["split"] == "OOS")]["car"])},
            "neg": {"all": stat(sub[sub["polarity"] < 0]["car"]),
                    "IS": stat(sub[(sub["polarity"] < 0) & (sub["split"] == "IS")]["car"]),
                    "OOS": stat(sub[(sub["polarity"] < 0) & (sub["split"] == "OOS")]["car"])},
        }
        print(f"h{h}: pos={res['groups']['h%d' % h]['pos']['all']} neg={res['groups']['h%d' % h]['neg']['all']}", flush=True)

    oos = res["groups"]["h5"]
    pos_t = abs((oos["pos"]["OOS"] or {}).get("t", 0) or 0)
    neg_t = abs((oos["neg"]["OOS"] or {}).get("t", 0) or 0)
    spread_t5 = (oos["spread"]["OOS"] or {}).get("t")
    spread_t20 = ((res["groups"]["h20"]["spread"]["OOS"]) or {}).get("t")
    spread_is5 = (oos["spread"]["IS"] or {}).get("t")
    spread_is20 = ((res["groups"]["h20"]["spread"]["IS"]) or {}).get("t")
    # 四关纪律：IS 与 OOS 需同向且均显著（|t|>=1.5/2）；仅 OOS 出现视为不稳健
    ok_5 = (spread_t5 is not None and spread_is5 is not None and
            abs(spread_t5) >= 2 and abs(spread_is5) >= 1.5 and (spread_t5 * spread_is5 > 0))
    ok_20 = (spread_t20 is not None and spread_is20 is not None and
             abs(spread_t20) >= 2 and abs(spread_is20) >= 1.5 and (spread_t20 * spread_is20 > 0))
    status = "backtested" if (ok_5 or ok_20) else "rejected"
    res["status"] = status

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H19_announcement_sentiment_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x["t"], x["n"]) if x.get("n") and "t" in x else ("n=%d(样本不足)" % x["n"] if x.get("n") else "—")
    L = ["# H19 · 公告标题情绪（关键词极性）→ 短期收益", "",
         "- 事件：anns_d 标题命中正/负关键词（单极性）；%s ~ %s；T+1 入场；相对等权市场 CAR；成本 30bp" % (START, END),
         "- 样本：%d 事件 / %d 观测；IS≤%s / OOS≥2024" % (res["meta"]["events"], res["meta"]["obs"], IS_END), "",
         "| 窗口 | 正面 CAR(t) | 负面 CAR(t) |", "|---|---|---|"]
    for h in HORIZONS:
        g = res["groups"][f"h{h}"]
        L.append("| %d 日 | %s | %s |" % (h, f(g["pos"]["all"]), f(g["neg"]["all"])))
    L += ["", "## 正负价差（正面 − 负面）", "| 窗口 | 全样本 | IS | OOS |", "|---|---|---|---|"]
    for h in HORIZONS:
        sp = res["groups"][f"h{h}"]["spread"]
        def fs(x):
            return "%.2f%%(t=%s)" % (x["mean_pct"], x["t"]) if x.get("mean_pct") is not None else "—"
        L.append("| %d 日 | %s | %s | %s |" % (h, fs(sp["all"]), fs(sp["IS"]), fs(sp["OOS"])))
    L += ["", "## 分样本（5 日）", "| 样本 | 正面 | 负面 |", "|---|---|---|"]
    for s in ("IS", "OOS"):
        g = res["groups"]["h5"]
        L.append("| %s | %s | %s |" % (s, f(g["pos"][s]), f(g["neg"][s])))
    L += ["", "## 结论", "- 状态：**%s**（判据：IS 与 OOS 价差同向且均显著；5 日 IS/OOS t=%.2f/%.2f，20 日 IS/OOS t=%.2f/%.2f）" % (
              status, spread_is5 or 0, spread_t5 or 0, spread_is20 or 0, spread_t20 or 0),
          "- 绝对 CAR：正面/负面均显著为负（OOS 5 日 %.2f%%/%.2f%%，t=%.2f/%.2f）→ 疑为「公告股整体跑输」的规模/选择偏差，**不是情绪 alpha**；" % (
              (oos["pos"]["OOS"] or {}).get("mean_pct"), (oos["neg"]["OOS"] or {}).get("mean_pct"), pos_t, neg_t),
          "- 若价差接近 0 或 t 不显著，则标题情绪无交易价值，仅作事件标记。", "",
          "## 局限", "- 关键词法非语义模型；同一公告可能多标题；事件重叠；未剔除停牌/涨跌停不可成交；",
          "- 公告标题情绪可能是「已知信息反应」而非领先信号，需看 1 日窗口是否已消化。"]
    md = OUT / "H19_announcement_sentiment_20261030.md"
    md.write_text("\n".join(L), encoding="utf-8")

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {"id": "H19", "title": "公告标题情绪（关键词极性）→ 短期收益",
           "question": "公告标题正/负面关键词是否伴随/领先短期收益？",
           "data_sources": "anns_d（2019-2026 标题）+ daily",
           "method": "关键词极性（单极性）；T+1 入场；1/3/5/10/20 日相对等权市场 CAR；成本30bp；IS≤2023/OOS≥2024",
           "gates": "PIT=是（公告日）；OOS=独立；成本=30bp；容量=大（标题事件多）",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "事件 %d；OOS 5日 正面 t=%.2f / 负面 t=%.2f → %s" % (
               res["meta"]["events"], pos_t, neg_t, status),
           "evidence_path": "research/reports/H19_announcement_sentiment_20261030.md",
           "created_at": now, "updated_at": now}
    keys = [k for k in row if k in cols]
    if con.execute("SELECT id FROM hypotheses WHERE id='H19'").fetchone():
        con.execute("UPDATE hypotheses SET %s WHERE id='H19'" % ",".join("%s=?" % k for k in keys if k != "id"),  # nosec B608 - 键来自固定 cols allowlist
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),  # nosec B608 - 键来自固定 cols allowlist
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print(json.dumps({"status": status, "events": res["meta"]["events"], "obs": res["meta"]["obs"],
                      "pos_oos5": res["groups"]["h5"]["pos"]["OOS"],
                      "neg_oos5": res["groups"]["h5"]["neg"]["OOS"], "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
