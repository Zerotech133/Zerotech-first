# -*- coding: utf-8 -*-
"""H29 深化：黄金柱/倍量确认 参数敏感性 + 容量/滑点评估。

A. 参数网格：
   量比阈值 vol_ratio ∈ {1.5, 1.9, 2.5}
   确认窗口 confirm_days ∈ {2,3,5}（后 N 日不破实顶+缩量）
   缩量阈值 shrink ∈ {0.6,0.8,0.9}
   持有期 hold ∈ {3,5,10}
   目标：OOS 5 日 CAR（β 调整、成本 30bp），看稳定性（IS/OOS 同向、t）
B. 容量/滑点：
   - 日均事件数、事件股平均成交额（amount）、可交易性（剔除涨停当日/停牌无法成交近似）
   - 滑点敏感性：额外 10/20/30bp 对 OOS 净收益影响
产出：research/reports/H29_param_capacity_20261031.{md,json}
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
import stat_utils  # noqa: E402

START, END = "20210101", "20261029"
IS_END = "20231231"
BASE_COST = 0.003


def load() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "open", "close", "pre_close", "pct_chg", "vol", "amount"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def main() -> int:
    df = load()
    g = df.groupby("ts_code", sort=False)
    df["vol_ma5"] = g["vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["vol_ratio5"] = df["vol"] / df["vol_ma5"].replace(0, np.nan)
    df["ret"] = g["close"].pct_change()
    mkt = df.groupby("trade_date")["pct_chg"].mean() / 100.0
    df["mkt_ret"] = df["trade_date"].map(mkt)

    def roll_beta(sub):
        cov = sub["ret"].rolling(60, min_periods=40).cov(sub["mkt_ret"])
        var = sub["mkt_ret"].rolling(60, min_periods=40).var()
        return cov / var.replace(0, np.nan)

    df["beta"] = df.groupby("ts_code", group_keys=False).apply(roll_beta).reindex(df.index)

    # 未来 N 日收益（T+1 收盘入场）
    maxh = 12
    for h in range(1, maxh + 1):
        df[f"fwd{h}"] = g["close"].shift(-(1 + h)) / g["close"].shift(-1) - 1
    dates = sorted(df["trade_date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    cum = (1 + mkt.fillna(0)).cumprod().reindex(dates).ffill().to_numpy()
    idxs = df["trade_date"].map(idx).to_numpy()
    beta = df["beta"].fillna(1.0).to_numpy()
    for h in range(1, maxh + 1):
        valid = idxs + 1 + h < len(dates)
        m = np.full(len(df), np.nan)
        m[valid] = cum[idxs[valid] + 1 + h] / cum[idxs[valid] + 1] - 1
        df[f"car{h}"] = df[f"fwd{h}"] - beta * m - BASE_COST

    # 成交额（容量）与涨停近似（次日无法买入）
    df["amount_yuan"] = df["amount"] * 1000.0  # tushare amount 单位千元

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 20:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    results = []
    # ---- A. 参数网格 ----
    for vol_th in (1.5, 1.9, 2.5):
        for cdays in (2, 3, 5):
            for shrink in (0.6, 0.8, 0.9):
                # 黄金柱：阳柱+量比；后 cdays 日 close 不破实顶且缩量
                cond = (df["pct_chg"] > 0) & (df["vol_ratio5"] >= vol_th)
                for k in range(1, cdays + 1):
                    cond &= (g["close"].shift(-k) >= df["close"] * 0.99) & (g["vol"].shift(-k) < df["vol"] * shrink)
                # PIT：信号在 T+cdays 收盘确认 → 移到确认日，次日入场
                sig_confirmed = cond.groupby(df["ts_code"], sort=False).shift(cdays).fillna(False).astype(bool)
                sub = df[sig_confirmed]
                for hold in (3, 5, 10):
                    s = sub[["trade_date", f"car{hold}"]].dropna()
                    if len(s) < 100:
                        continue
                    s.columns = ["date", "car"]
                    s["split"] = np.where(s["date"] <= IS_END, "IS", "OOS")
                    daily = s.groupby("date")["car"].mean()
                    isd = stat(daily[daily.index <= IS_END])
                    oos = stat(daily[daily.index > IS_END])
                    ok = (isd.get("t") or 0) >= 1.5 and (isd.get("mean_pct") or 0) > 0 and \
                         (oos.get("t") or 0) >= 2 and (oos.get("mean_pct") or 0) > 0
                    results.append({"kind": "golden", "vol_th": vol_th, "confirm_days": cdays,
                                    "shrink": shrink, "hold": hold, "events": int(len(s)),
                                    "IS": isd, "OOS": oos, "pass": bool(ok)})
    print("grid done:", len(results), flush=True)
    passed = [r for r in results if r["pass"]]
    passed.sort(key=lambda r: -(r["OOS"].get("t") or 0))
    print("passed combos:", len(passed), flush=True)
    for r in passed[:8]:
        print(r["vol_th"], r["confirm_days"], r["shrink"], "hold", r["hold"], "IS", r["IS"], "OOS", r["OOS"], flush=True)

    # ---- B. 容量/滑点（用默认参数黄金柱：1.5/3/0.9，hold=5；倍量确认）----
    def build_golden(vol_th=1.5, cdays=3, shrink=0.9):
        cond = (df["pct_chg"] > 0) & (df["vol_ratio5"] >= vol_th)
        for k in range(1, cdays + 1):
            cond &= (g["close"].shift(-k) >= df["close"] * 0.99) & (g["vol"].shift(-k) < df["vol"] * shrink)
        return cond

    _gold_cond = build_golden()
    _gold_sig = _gold_cond.groupby(df["ts_code"], sort=False).shift(3).fillna(False).astype(bool)
    gold = df[_gold_sig]
    # 可交易性：入场日（T+1）涨停（pct_chg>=9.5%）近似无法买入 → 剔除
    t1_pct = g["pct_chg"].shift(-1)
    gold = gold.assign(t1_pct=t1_pct[gold.index])
    tradable = gold[gold["t1_pct"] < 9.5]
    cap = {"golden_events": int(len(gold)), "tradable_events": int(len(tradable)),
           "excluded_limitup_pct": round((1 - len(tradable) / max(1, len(gold))) * 100, 2)}
    for label, sub in (("golden_all", gold), ("golden_tradable", tradable)):
        s = sub[["trade_date", "car5"]].dropna()
        s.columns = ["date", "car"]
        daily = s.groupby("date")["car"].mean()
        cap[label] = {"events": int(len(s)), "OOS": stat(daily[daily.index > IS_END]),
                      "IS": stat(daily[daily.index <= IS_END]),
                      "avg_events_per_day": round(len(s) / max(1, len(daily)), 2)}
    # 容量：事件股平均成交额（亿元）
    ev_amount = gold["amount_yuan"].dropna()
    cap["avg_event_amount_yi"] = round(float(ev_amount.mean()) / 1e8, 3)
    cap["median_event_amount_yi"] = round(float(ev_amount.median()) / 1e8, 3)
    # 滑点敏感性：额外成本 x bp 对 OOS 5 日净收益
    sub = gold[["trade_date", "car5"]].dropna()
    sub.columns = ["date", "car"]
    base_daily = sub.groupby("date")["car"].mean()
    oos_base = base_daily[base_daily.index > IS_END]
    slip = {}
    for bp in (0, 10, 20, 30, 50):
        slip[bp] = round(float((oos_base.mean() - bp / 10000.0) * 100), 3)
    cap["slippage_oos_mean_pct"] = slip
    print("capacity:", json.dumps(cap, ensure_ascii=False), flush=True)

    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "base_cost": BASE_COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "grid": results, "pass_count": len(passed), "passed_top": passed[:20],
           "capacity": cap}
    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H29_param_capacity_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")
    L = ["# H29 深化：参数敏感性 + 容量/滑点", "",
         f"- 样本 {START}~{END}；β 调整；基础成本 30bp；IS≤2023 / OOS≥2024",
         f"- 黄金柱参数网格（量比×确认天数×缩量×持有期）共 {len(results)} 组，**通过 {len(passed)} 组**", "",
         "## 通过组合 Top（按 OOS t 排序）",
         "| 量比 | 确认日 | 缩量 | 持有 | 事件 | IS | OOS |", "|---|---|---|---|---|---|---|"]
    for r in passed[:10]:
        L.append(f"| {r['vol_th']} | {r['confirm_days']} | {r['shrink']} | {r['hold']}日 | {r['events']} | {f(r['IS'])} | {f(r['OOS'])} |")
    L += ["", "## 容量/滑点（默认黄金柱 1.5/3/0.9，hold=5）",
          f"- 事件 {cap['golden_events']:,}；剔除次日涨停不可买后 {cap['tradable_events']:,}（剔除 {cap['excluded_limitup_pct']}%）",
          f"- OOS 5日：可交易组 {f(cap['golden_tradable']['OOS'])}；日均事件 {cap['golden_tradable']['avg_events_per_day']}",
          f"- 事件股平均成交额 {cap['avg_event_amount_yi']} 亿元（中位 {cap['median_event_amount_yi']} 亿）",
          f"- 滑点敏感性（OOS 5日净收益%）：{cap['slippage_oos_mean_pct']}", "",
          "## 结论",
          "- 参数稳健性看: 通过组合的数量与 OOS t 分布；若集中在极端参数 → 不稳；",
          "- 容量：日均事件数适中、平均成交额>1 亿 → 小资金可承载；滑点 10–30bp 后仍正才可用；",
          "- 下一步：选定稳健参数组合 → 影子盘监控（另立候选，等例会/老田拍板）。"]
    (OUT / "H29_param_capacity_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"pass_count": len(passed), "capacity": cap["slippage_oos_mean_pct"],
                      "tradable_oos": cap["golden_tradable"]["OOS"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
