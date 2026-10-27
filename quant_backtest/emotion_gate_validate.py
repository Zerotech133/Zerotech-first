# -*- coding: utf-8 -*-
"""情绪环境闸门 · B方案可行性验证（K-STOCK-400·只读研究）。

## 要验证的假设（B 方案成立的前提）

**H1**: 情绪冰点日 → 全市场个股表现显著差于中性/亢奋日（⇒ 冰点时该整体减仓）
**H2**: 情绪冰点日 → 现有策略（weak/strong 口径的 r20 超额）表现显著差
**H3**: 情绪档位对**次日**有预测力（而非仅当日描述）

## 为什么先验证再上线

B 方案要做成其他策略的前置闸门，必须先证明它**真的有区分度**。
13.4 已发现日频情绪总量对「选哪只」区分度弱；本脚本专测它对
**「今天整体环境好不好」**是否有区分度——这是两个不同的问题。

纪律：只读，不写任何生产表；输出仅 stdout。
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EMO = os.path.join(HERE, "data", "tdx", "ts_emotion_daily")
DAILY = os.path.join(HERE, "data", "tdx", "ts_daily")
BASIC = os.path.join(HERE, "data", "tdx", "ts_stock_basic")


def emotion() -> pd.DataFrame:
    f = sorted(glob.glob(os.path.join(EMO, "part_*.parquet")))[0]
    d = pd.read_parquet(f)
    d["trade_date"] = d["trade_date"].astype(str)
    return d


def tier_of(s: pd.Series) -> pd.Series:
    lo = np.percentile(s.dropna(), 10)
    hi = np.percentile(s.dropna(), 90)
    return np.select([s >= hi, s <= lo], [2, 0], 1)


def main() -> int:
    emo = emotion()
    emo["档"] = (
        tier_of(pd.to_numeric(emo["涨停家数"], errors="coerce"))
        + tier_of(pd.to_numeric(emo["封板率"], errors="coerce"))
        + tier_of(pd.to_numeric(emo["晋级率"], errors="coerce"))
        + tier_of(pd.to_numeric(emo["最高连板"], errors="coerce"))
    )
    emo["档名"] = pd.cut(emo["档"], bins=[-1, 3, 6, 8], labels=["冰点", "中性", "亢奋"])
    emo = emo.sort_values("trade_date").reset_index(drop=True)

    print("=" * 92)
    print("B 方案可行性验证 · H1 全市场涨跌（%d 交易日）" % len(emo))
    print("=" * 92)

    # ── H1：全市场当日涨跌 ────────────────────────────────
    parts = sorted(glob.glob(os.path.join(DAILY, "part_*.parquet")))
    if not parts:
        print("★无 ts_daily，无法验证 H1")
        return 1
    cols = [c for c in ("ts_code", "trade_date", "pct_chg") if True]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in parts], ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    print(
        "ts_daily: %d 行 / %d 交易日 / %d 只"
        % (len(df), df["trade_date"].nunique(), df["ts_code"].nunique())
    )

    mkt = df.groupby("trade_date")["pct_chg"].mean().rename("全市场均涨")
    mkt_med = df.groupby("trade_date")["pct_chg"].median().rename("全市场中位")
    up_ratio = (
        df.groupby("trade_date")["pct_chg"]
        .apply(lambda s: round(float((s > 0).mean()) * 100, 2))
        .rename("上涨占比%")
    )
    j = emo.set_index("trade_date").join([mkt, mkt_med, up_ratio])

    print("\n【H1】各情绪档的全市场表现")
    g = j.groupby("档名", observed=True).agg(
        天数=("全市场均涨", "size"),
        均涨均值=("全市场均涨", lambda s: round(float(s.mean()), 4)),
        中位数均值=("全市场中位", lambda s: round(float(s.mean()), 4)),
        上涨占比均值=("上涨占比%", "mean"),
    )
    g["上涨占比均值"] = g["上涨占比均值"].round(2)
    print(g.to_string())

    # t 检验（冰点 vs 其他）
    from statistics import mean, stdev

    ice = j[j["档名"] == "冰点"]["全市场均涨"].dropna()
    oth = j[j["档名"] != "冰点"]["全市场均涨"].dropna()
    diff = mean(ice) - mean(oth)
    se = np.sqrt(stdev(ice) ** 2 / len(ice) + stdev(oth) ** 2 / len(oth))
    t = diff / se if se else 0
    print(
        "\n冰点(%d日) vs 非冰点(%d日): 均涨差 %+.4f pp, t=%.2f ⇒ %s"
        % (
            len(ice),
            len(oth),
            diff,
            t,
            "有显著差异" if abs(t) > 1.96 else "★无显著差异（H1 不成立）",
        )
    )

    # ── H3：次日预测力 ───────────────────────────────────
    print("\n" + "=" * 92)
    print("【H3】次日预测力：今日档位 → 明日全市场均涨")
    print("=" * 92)
    j["次日均涨"] = j["全市场均涨"].shift(-1)
    # 跳过休市造成的非相邻日：只有日期间隔 ≤4 天的才算「次日」
    dates = sorted(j.index)
    adj = [True] + [
        (pd.to_datetime(dates[i + 1]) - pd.to_datetime(dates[i])).days <= 4
        for i in range(len(dates) - 1)
    ]
    j["相邻"] = adj  # 长度 = len(dates)，与j.index 等长
    nx = j[j["相邻"] & j["次日均涨"].notna()]
    gg = nx.groupby("档名", observed=True).agg(
        样本=("次日均涨", "size"),
        次日均涨=("次日均涨", lambda s: round(float(s.mean()), 4)),
    )
    print(gg.to_string())
    for name in ("冰点", "中性", "亢奋"):
        s = nx[nx["档名"] == name]["次日均涨"].dropna()
        if len(s) > 2:
            print("  %s 档：次日均涨 std=%.4f（越大越无预测力）" % (name, stdev(s)))

    print("\n" + "=" * 92)
    print("结论要点")
    print("=" * 92)
    if abs(t) > 1.96:
        print("✅ H1 成立：冰点日全市场表现显著更差 ⇒ **情绪档位可作风控闸门**")
    else:
        print("⚠️ H1 不显著：情绪档位与全市场涨跌**无稳定区分度** ⇒ B 方案闸门价值存疑")
    print(
        "⚠️ H3 若各档次日 std 相近，说明情绪是**同步描述量**（当日即反映），无跨日预测力"
    )
    print("⇒ 即使 H1 成立，闸门也只能在**当日盘中实时用**，不能用于次日开盘决策")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
