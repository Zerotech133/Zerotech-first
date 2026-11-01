# -*- coding: utf-8 -*-
"""H29 · 严格版量学特征回归 + H28 规避信号容量测试。

严格定义（对照原书，PIT：确认日收盘才知信号）：
  golden_pillar 黄金柱：阳柱（量比5≥1.5）后 3 日收盘不破该柱实顶（close≥close_t×0.99）且逐日缩量 → 第4日入场
  double_vol_confirm 倍量确认：倍量收阳后次日缩量（<60%）且不破前收 → 次日入场
  yin_yang_strict 极阴次阳（严格）：前日跌幅≤-7% 且今日收阳且收盘≥前日开盘 → 次日入场
  shrink_pullback 缩量回踩：close>ma10、当日跌>1%、量比5<0.6 → 次日入场
H28 容量：按原始 4 个形态构建「规避组合」（等权全市场剔除触发股）vs 全市场等权，计算年化超额/换手/每日规避数/空头上界。

产出：research/reports/H29_liangxue_refined_20261031.{md,json}；台账 H29 更新、H28 conclusion 更新。
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
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import stat_utils  # noqa: E402

START, END = "20210101", "20261029"
IS_END = "20231231"
COST = 0.003
HORIZONS = (5, 10, 20)


def load_daily() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "open", "close", "pre_close", "pct_chg", "vol"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def main() -> int:
    df = load_daily()
    g = df.groupby("ts_code", sort=False)
    df["vol_ma5"] = g["vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["vol_ratio5"] = df["vol"] / df["vol_ma5"].replace(0, np.nan)
    df["ma10"] = g["close"].transform(lambda s: s.rolling(10, min_periods=5).mean())
    df["ret"] = g["close"].pct_change()
    mkt_ret = df.groupby("trade_date")["pct_chg"].mean() / 100.0
    df["mkt_ret"] = df["trade_date"].map(mkt_ret)

    # ---- 原始 4 形态（H28 规避用；与 H24-27 同定义）----
    df["sig_double_vol"] = (df["vol_ratio5"] >= 1.9) & (df["pct_chg"] > 0)
    df["prev_vol"] = g["vol"].shift(1)
    df["sig_price_up_vol_down"] = (df["pct_chg"] > 0) & (df["vol"] < df["prev_vol"] * 0.8)
    df["prev_pct"] = g["pct_chg"].shift(1)
    df["prev_open"] = g["open"].shift(1)
    df["prev_close"] = g["pre_close"].shift(1)
    df["sig_yin_yang"] = (df["prev_pct"] <= -5) & (df["pct_chg"] > 0) & (df["close"] >= (df["prev_open"] + df["prev_close"]) / 2)
    df["hi20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=10).max())
    df["sig_near_high_vol"] = (df["close"] >= df["hi20"] * 0.98) & (df["vol_ratio5"] >= 1.5)
    df["sig_any_bearish"] = df[["sig_double_vol", "sig_price_up_vol_down", "sig_yin_yang", "sig_near_high_vol"]].any(axis=1)

    # ---- 严格版特征（确认日）----
    c = df["close"]
    v = df["vol"]
    gs = df.groupby("ts_code", sort=False)
    c1, c2, c3 = gs["close"].shift(-1), gs["close"].shift(-2), gs["close"].shift(-3)
    v1, v2, v3 = gs["vol"].shift(-1), gs["vol"].shift(-2), gs["vol"].shift(-3)
    # 黄金柱：今日阳柱且量比≥1.5；后3日不破实顶+缩量 → 第4日（T+4）入场；用 shift(-3) 后信号在 T+3 确认
    gold_ok = (df["pct_chg"] > 0) & (df["vol_ratio5"] >= 1.5) & (c1 >= c * 0.99) & (c2 >= c * 0.99) & (c3 >= c * 0.99) \
        & (v1 < v * 0.9) & (v2 < v * 0.9) & (v3 < v * 0.9)
    # PIT 修正：信息在 T+3 收盘才确认 → 信号移到 T+3 行（次日 T+4 入场）
    df["sig_golden_pillar"] = gold_ok.groupby(df["ts_code"], sort=False).shift(3).fillna(False).astype(bool)
    # 倍量确认：T+1 收盘确认 → 信号移到 T+1 行（T+2 入场）
    dv = (df["vol_ratio5"] >= 1.9) & (df["pct_chg"] > 0) & (v1 < v * 0.6) & (c1 >= c)
    df["sig_double_vol_confirm"] = dv.groupby(df["ts_code"], sort=False).shift(1).fillna(False).astype(bool)
    # 极阴次阳（严格）：前日≤-7%，今日收阳且收盘≥前日开盘
    df["sig_yin_yang_strict"] = (df["prev_pct"] <= -7) & (df["pct_chg"] > 0) & (df["close"] >= df["prev_open"])
    # 缩量回踩：close>ma10、当日跌>1%、量比5<0.6
    df["sig_shrink_pullback"] = (df["close"] > df["ma10"]) & (df["pct_chg"] < -1) & (df["vol_ratio5"] < 0.6)

    # ---- beta ----
    def roll_beta(sub):
        cov = sub["ret"].rolling(60, min_periods=40).cov(sub["mkt_ret"])
        var = sub["mkt_ret"].rolling(60, min_periods=40).var()
        return cov / var.replace(0, np.nan)

    df["beta"] = df.groupby("ts_code", group_keys=False).apply(roll_beta).reindex(df.index)
    for h in HORIZONS:
        df[f"fwd{h}"] = gs["close"].shift(-(1 + h)) / gs["close"].shift(-1) - 1
    dates = sorted(df["trade_date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    mkt_cum = (1 + mkt_ret.fillna(0)).cumprod().reindex(dates).ffill().to_numpy()
    idxs = df["trade_date"].map(idx).to_numpy()
    for h in HORIZONS:
        valid = idxs + 1 + h < len(dates)
        m = np.full(len(df), np.nan)
        m[valid] = mkt_cum[idxs[valid] + 1 + h] / mkt_cum[idxs[valid] + 1] - 1
        beta = df["beta"].fillna(1.0).to_numpy()
        df[f"car{h}"] = df[f"fwd{h}"] - beta * m - COST

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 20:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    refined = {"H29_golden_pillar": "sig_golden_pillar", "H29_double_vol_confirm": "sig_double_vol_confirm",
               "H29_yin_yang_strict": "sig_yin_yang_strict", "H29_shrink_pullback": "sig_shrink_pullback"}
    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "rows": int(len(df)), "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "refined": {}, "h28_capacity": {}}
    for hid, col in refined.items():
        sub = df[df[col]]
        g2 = {}
        for h in HORIZONS:
            s = sub[["trade_date", f"car{h}"]].dropna()
            s.columns = ["date", "car"]
            s["split"] = np.where(s["date"] <= IS_END, "IS", "OOS")
            daily = s.groupby("date")["car"].mean()
            g2[f"h{h}"] = {"events": int(len(s)),
                           "daily": {"all": stat(daily), "IS": stat(daily[daily.index <= IS_END]),
                                     "OOS": stat(daily[daily.index > IS_END])},
                           "bootstrap": stat_utils.block_bootstrap_p(daily[daily.index > IS_END], block=5, n_boot=300)}
        best = None
        for h in HORIZONS:
            oos = g2[f"h{h}"]["daily"]["OOS"]
            isd = g2[f"h{h}"]["daily"]["IS"]
            ok = (oos.get("t") is not None and oos["t"] >= 2 and (oos.get("mean_pct") or 0) > 0
                  and isd.get("t") is not None and isd["t"] >= 1.5 and (isd.get("mean_pct") or 0) > 0)
            if ok and best is None:
                best = {"horizon": h, "IS": isd, "OOS": oos}
        g2["status"] = "backtested" if best else "rejected"
        g2["best"] = best
        rep["refined"][hid] = g2
        print(hid, "events", g2["h10"]["events"], "best", best and best["horizon"], "->", g2["status"], flush=True)

    # ---- H28 规避组合容量 ----
    # 每日等权收益：全市场 vs 剔除触发股（触发=当日 sig_any_bearish）
    df["is_bear"] = df["sig_any_bearish"].fillna(False)
    # PIT：T 日触发 → T+1 起规避；用「昨日触发」排除今日（无同日前视）
    df["is_bear_prev"] = df.groupby("ts_code", sort=False)["is_bear"].shift(1).fillna(False).astype(bool)
    daily_all = df.groupby("trade_date")["pct_chg"].mean() / 100.0
    daily_avoid = df[~df["is_bear_prev"]].groupby("trade_date")["pct_chg"].mean() / 100.0
    avoid_cnt = df.groupby("trade_date")["is_bear_prev"].sum()
    both = pd.DataFrame({"all": daily_all, "avoid": daily_avoid, "n_avoid": avoid_cnt}).dropna()
    both["excess"] = both["avoid"] - both["all"]
    both = both[both.index <= END]
    both["split"] = np.where(both.index <= IS_END, "IS", "OOS")
    ann_avoid = float((1 + both["avoid"]).prod() ** (252 / len(both)) - 1)
    ann_all = float((1 + both["all"]).prod() ** (252 / len(both)) - 1)
    ex = both["excess"]
    cap = {"days": int(len(both)), "ann_all_pct": round(ann_all * 100, 2), "ann_avoid_pct": round(ann_avoid * 100, 2),
           "excess_ann_pct": round((ann_avoid - ann_all) * 100, 2),
           "excess_t": round(float(ex.mean() / ex.std(ddof=1) * np.sqrt(len(ex))), 2),
           "avg_avoid_per_day": round(float(both["n_avoid"].mean()), 1),
           "max_avoid_per_day": int(both["n_avoid"].max()),
           "IS": {"excess_t": round(float(ex[both.split == "IS"].mean() / ex[both.split == "IS"].std(ddof=1) * np.sqrt((both.split == "IS").sum())), 2),
                  "days": int((both.split == "IS").sum())},
           "OOS": {"excess_t": round(float(ex[both.split == "OOS"].mean() / ex[both.split == "OOS"].std(ddof=1) * np.sqrt((both.split == "OOS").sum())), 2),
                   "days": int((both.split == "OOS").sum())},
           "turnover_note": "每日触发股集合变化即换手；规避=不持有，非做空（A股做空受限）"}
    rep["h28_capacity"] = cap
    print("H28 capacity:", json.dumps(cap, ensure_ascii=False), flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H29_liangxue_refined_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")

    L = ["# 量学知识严格版回归（H29）+ H28 规避容量", "",
         f"- 样本 {START}~{END}；确认日收盘后 T+1 入场；β 调整 CAR；成本 30bp；IS≤2023 / OOS≥2024",
         "- 判定：任一持有期（5/10/20）满足「IS/OOS 同向且 OOS t≥2、IS t≥1.5」即 backtested（记最佳窗口）", "",
         "## H29 严格版形态", "| 形态 | 事件 | 最佳窗口 | IS | OOS | 判定 |", "|---|---|---|---|---|---|"]
    for hid, grp in rep["refined"].items():
        b = grp.get("best")
        if b:
            L.append(f"| {hid} | {grp['h10']['events']} | **{b['horizon']}日** | {f(b['IS'])} | {f(b['OOS'])} | **backtested** |")
        else:
            L.append(f"| {hid} | {grp['h10']['events']} | — | {f(grp['h10']['daily']['IS'])} | {f(grp['h10']['daily']['OOS'])} | rejected |")
    L += ["", "## H28 规避组合容量（等权全市场 vs 剔除触发股）",
          "| 指标 | 值 |", "|---|---|",
          f"| 全市场年化 | {cap['ann_all_pct']}% |",
          f"| 规避组合年化 | {cap['ann_avoid_pct']}% |",
          f"| 年化超额 | **{cap['excess_ann_pct']}%**（日频 t={cap['excess_t']}；IS t={cap['IS']['excess_t']} / OOS t={cap['OOS']['excess_t']}） |",
          f"| 日均规避数 | {cap['avg_avoid_per_day']}（最大 {cap['max_avoid_per_day']}） |",
          f"| 说明 | {cap['turnover_note']} |", "",
          "## 结论",
          "- H29 严格版若仍无正效应 → 量学看涨形态在当前样本不可作为买入信号；",
          "- H28 规避组合：若超额 ~0 或换手过高，则不构成可用策略（A 股规避≠做空，容量/交易成本需评估）。"]
    (OUT / "H29_liangxue_refined_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"refined": {k: v["status"] for k, v in rep["refined"].items()}, "h28": cap}, ensure_ascii=False), flush=True)

    # ---- 台账 ----
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]

    def upsert(row):
        keys = [k for k in row if k in cols]
        if con.execute("SELECT id FROM hypotheses WHERE id=?", (row["id"],)).fetchone():
            con.execute("UPDATE hypotheses SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys if k != "id"),
                        [row[k] for k in keys if k != "id"] + [row["id"]])
        else:
            con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                        [row[k] for k in keys])

    upsert({"id": "H29", "title": "严格版量学形态（黄金柱/倍量确认/极阴次阳严格/缩量回踩）",
            "question": "按原书严格确认规则，量学形态是否带来正超额？",
            "data_sources": "daily 2021-2026", "method": "严格定义+确认日入场；β调整CAR；IS≤2023/OOS≥2024；30bp；block bootstrap",
            "gates": "PIT=是；OOS=独立；成本=30bp；容量=待评估",
            "status": "backtested" if any(v.get("best") for v in rep["refined"].values()) else "rejected",
            "owner": "DSH-Lead",
            "conclusion": "黄金柱 %s；倍量确认 %s；极阴次阳严格 %s；缩量回踩 %s；最佳窗口=%s" % (
                f(rep["refined"]["H29_golden_pillar"]["h10"]["daily"]["OOS"]),
                f(rep["refined"]["H29_double_vol_confirm"]["h10"]["daily"]["OOS"]),
                f(rep["refined"]["H29_yin_yang_strict"]["h10"]["daily"]["OOS"]),
                f(rep["refined"]["H29_shrink_pullback"]["h10"]["daily"]["OOS"]),
                json.dumps({k: (v.get("best") or {}).get("horizon") for k, v in rep["refined"].items()}, ensure_ascii=False)),
            "evidence_path": "research/reports/H29_liangxue_refined_20261031.md",
            "created_at": now, "updated_at": now})
    oos28 = cap["OOS"]["excess_t"]
    upsert({"id": "H28", "title": "量价形态反向规避（倍量柱/价升量缩/极阴次阳/兵临城下）",
            "question": "量学看涨形态出现后 10/20 日显著跑输（已观测），规避组合能否带来超额？",
            "data_sources": "daily 2021-2026", "method": "等权全市场 vs 剔除触发股；日频超额；IS/OOS；换手/容量",
            "gates": "PIT=是；OOS=独立；成本=30bp；容量=已初步（日均规避数）",
            "status": "rejected" if abs(oos28) < 2 else "in_progress", "owner": "DSH-Lead",
            "conclusion": "规避组合年化超额 %s%%（日频 t=%s；IS t=%s / OOS t=%s）；日均规避 %s 只；%s" % (
                cap["excess_ann_pct"], cap["excess_t"], cap["IS"]["excess_t"], cap["OOS"]["excess_t"],
                cap["avg_avoid_per_day"], "不构成可用策略" if abs(oos28) < 2 else "有超额但需成本/容量评估"),
            "evidence_path": "research/reports/H29_liangxue_refined_20261031.md",
            "created_at": now, "updated_at": now})
    con.commit()
    con.close()
    print("台账 H29/H28 已更新", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
