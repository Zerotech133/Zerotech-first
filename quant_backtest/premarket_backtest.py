# -*- coding: utf-8 -*-
"""盘前历史场景回放（A股指数近似版）：用本地 ashare 指数反推外围评分，对照次日开盘收益。

口径：
- 外围近似评分 = 前一日 A 股主要指数（上证/深成/创业板/科创50）涨跌幅加权映射
- 次日开盘收益 = ashare 全市场等权（open vs 前收，剔停牌）
- 分桶：偏多(>=55)/中性(45-55)/偏空(<45)，看各桶次日开盘收益均值/胜率
"""
import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = os.path.join(HERE, "data", "ashare")
OUT = os.path.join(HERE, "reports", "premarket_backtest.json")

# A 股指数近似外围（权重对齐 GLOBAL_IDX 风格）
PROXY_IDX = [("000001_SH", "上证", .40), ("399001_SZ", "深成", .25),
             ("399006_SZ", "创业板", .20), ("000688_SH", "科创50", .15)]


def _clip(v, lo=-100, hi=100):
    return max(lo, min(hi, v))


def load_index_hist():
    """加载 A 股指数日线 → {code: {date: close}}。"""
    out = {}
    for code, _, _ in PROXY_IDX:
        path = os.path.join(ASHARE, code + ".parquet")
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path, columns=["trade_date", "close"])
        df = df.dropna()
        out[code] = dict(zip(
            df["trade_date"].astype(str).str.replace("-", ""),
            df["close"].astype(float)))
    return out


def proxy_score(hist, target_date):
    """用 target_date 及其前一交易日的 A 股指数反推外围近似评分。"""
    score, wsum = 0.0, 0.0
    for code, _, w in PROXY_IDX:
        h = hist.get(code, {})
        dates = sorted(d for d in h if d <= target_date)
        if len(dates) < 2:
            continue
        d0, d1 = dates[-1], dates[-2]
        pct = (h[d1] / h[d0] - 1) * 100
        score += w * _clip(pct * 18)
        wsum += w
    return round(score / wsum) if wsum else None


def ashare_open_returns(dates):
    """全市场等权开盘收益。"""
    frames = {}
    for f in sorted(glob.glob(os.path.join(ASHARE, "*.parquet"))):
        code = os.path.basename(f)[:-8].replace("_", ".")
        try:
            df = pd.read_parquet(f, columns=["trade_date", "open", "pre_close"])
            df = df.dropna(subset=["open", "pre_close"])
            df = df[df["pre_close"] > 0]
            frames[code] = dict(zip(
                df["trade_date"].astype(str).str.replace("-", ""),
                df["open"] / df["pre_close"] - 1))
        except Exception:
            continue
    out = {}
    for d in dates:
        rets = [v[d] for v in frames.values() if d in v]
        if len(rets) >= 1000:
            out[d] = round(float(np.mean(rets)) * 100, 3)
    return out


def run():
    hist = load_index_hist()
    if not hist:
        print("无指数数据")
        return
    trade_dates = sorted({d for h in hist.values() for d in h})
    print("交易日: %d" % len(trade_dates))

    open_ret = ashare_open_returns(trade_dates)
    print("有效交易日: %d" % len(open_ret))

    rows = []
    for d in trade_dates:
        if d not in open_ret:
            continue
        sc = proxy_score(hist, d)
        if sc is None:
            continue
        rows.append({"date": d, "score": sc, "open_ret": open_ret[d]})

    buckets = {"偏多(>=55)": [], "中性(45-55)": [], "偏空(<45)": []}
    for r in rows:
        s = r["score"]
        b = "偏多(>=55)" if s >= 55 else "偏空(<45)" if s < 45 else "中性(45-55)"
        buckets[b].append(r)

    print("\n=== 外围近似评分分桶 vs 次日开盘收益 ===")
    report = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "n_total": len(rows), "buckets": {},
              "note": "外围=A股指数近似（非真实隔夜）"}
    for name, rs in buckets.items():
        if not rs:
            print("%s: 无样本" % name)
            continue
        rets = [r["open_ret"] for r in rs]
        avg = float(np.mean(rets))
        win = sum(1 for x in rets if x > 0) / len(rets) * 100
        print("%s: n=%d 均=%.3f%% 胜率=%.0f%%" % (name, len(rs), avg, win))
        report["buckets"][name] = {"n": len(rs), "avg_open_ret": round(avg, 3),
                                    "win_rate": round(win, 1)}

    scores = [r["score"] for r in rows]
    rets = [r["open_ret"] for r in rows]
    corr = float(np.corrcoef(scores, rets)[0, 1])
    print("\n外围近似评分 vs 开盘收益 相关系数: %.3f" % corr)
    report["corr"] = round(corr, 3)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("报告已写入: %s" % OUT)


if __name__ == "__main__":
    run()
