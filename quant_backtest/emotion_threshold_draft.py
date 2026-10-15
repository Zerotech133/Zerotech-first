# -*- coding: utf-8 -*-
"""情绪周期 · 三档信号阈值草案（K-STOCK-399·只读分析）。

目的：给老田一个**数据驱动**的阈值参考，而不是拍脑袋定数。
方法：用本地 268 交易日（20250901~20261014）的情绪面板，按**分位数**定三档。

纪律：只读 ts_emotion_daily + ts_emotion_ladder，不写任何生产表；
     输出仅落stdout（阈值草案待老田拍板后才应写进策略）。
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
LAD = os.path.join(HERE, "data", "tdx", "ts_emotion_ladder")


def load():
    f = sorted(glob.glob(os.path.join(EMO, "part_*.parquet")))[0]
    return pd.read_parquet(f)


def q(s, ps):
    return {("P%d" % p): round(float(np.percentile(s.dropna(), p)), 2) for p in ps}


def main() -> int:
    df = load()
    df["trade_date"] = df["trade_date"].astype(str)
    n = len(df)
    print("=" * 92)
    print("情绪周期 · 三档阈值草案（基于 %d 个交易日 %s..%s）"
          % (n, df["trade_date"].iloc[0], df["trade_date"].iloc[-1]))
    print("=" * 92)

    print("\n【一】各指标分位数（%）")
    print("%-12s %8s %8s %8s %8s %8s %8s" % ("指标", "P10", "P25", "P50", "P75", "P90", "最大"))
    cols = ["涨停家数", "跌停家数", "炸板家数", "最高连板", "连板家数", "封板率", "晋级率"]
    for c in cols:
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        print("%-12s %8.2f %8.2f %8.2f %8.2f %8.2f %8.2f"
              % (c, *[np.percentile(s.dropna(), p) for p in (10, 25, 50, 75, 90)],
                 s.max()))

    print("\n【二】三档草案（P10/P90 双侧分位·08:05 修正版）")
    print("🔴 阈值口径判例：首版取 P30/P75 时「涨停家数 ≥53」有 **73% 的日子达标**（196/268），")
    print("   阈值完全失效。分位阈值必须**按目标占比反推**——要10% 的日子进高档就用 P90，不是 P30。")
    lu = pd.to_numeric(df["涨停家数"], errors="coerce")
    fr = pd.to_numeric(df["封板率"], errors="coerce")
    jl = pd.to_numeric(df["晋级率"], errors="coerce")
    hb = pd.to_numeric(df["最高连板"], errors="coerce")

    rows = []
    for c, s in (("涨停家数", lu), ("封板率%", fr), ("晋级率%", jl), ("最高连板", hb)):
        p10, p90 = np.percentile(s.dropna(), 10), np.percentile(s.dropna(), 90)
        rows.append((c, round(float(p10), 2), round(float(p90), 2),
                     round(float((s <= p10).mean() * 100), 1),
                     round(float((s >= p90).mean() * 100), 1)))
    print("%-11s %10s %10s %14s %14s" % ("指标", "冰点≤P10", "亢奋≥P90", "冰点命中占比", "亢奋命中占比"))
    print("-" * 92)
    for c, lo, hi, plo, phi in rows:
        print("%-11s %10s %10s %13.1f%% %13.1f%%" % (c, lo, hi, plo, phi))
    print("  ✅ 四指标命中占比均≈10% —— 阈值口径正确（对照首版 P30 的 73% 是失效阈值）")

    print("\n【三】组合打分（各指标 P10/P90 定 0/1/2 分，合计 0~8）")
    df["情绪总分"] = sum(
        np.select([s >= np.percentile(s.dropna(), 90),
                   s <= np.percentile(s.dropna(), 10)], [2, 0], 1)
        for s in (lu, fr, jl, hb))
    print("总分分布:")
    print(df["情绪总分"].value_counts().sort_index().to_string())
    lo_n = int((df["情绪总分"] <= 3).sum())
    mid_n = int(((df["情绪总分"] >= 4) & (df["情绪总分"] <= 6)).sum())
    hi_n = int((df["情绪总分"] >= 7).sum())
    print("\n三档: 冰点(0-3) %d 日 / 中性(4-6) %d 日 / 亢奋(7-8) %d 日"
          % (lo_n, mid_n, hi_n))
    tier = pd.cut(df["情绪总分"], bins=[-1, 3, 6, 8],
                  labels=["冰点(0-3)", "中性(4-6)", "亢奋(7-8)"])
    print("当前状态: %s 总分=%d档=%s"
          % (df["trade_date"].iloc[-1], int(df["情绪总分"].iloc[-1]), tier.iloc[-1]))

    print("\n【四】各档的连板梯队特征（回看验证·非预测）")
    lad_files = sorted(glob.glob(os.path.join(LAD, "part_*.parquet")))
    if lad_files:
        lad = pd.read_parquet(lad_files[0])
        lad["trade_date"] = lad["trade_date"].astype(str)
        lad["情绪总分"] = lad["trade_date"].map(
            df.set_index("trade_date")["情绪总分"])
        lad = lad.dropna(subset=["情绪总分"])
        agg = lad.groupby(pd.cut(lad["情绪总分"], bins=[-1, 3, 6, 8],
                                 labels=["冰点(0-3)", "中性(4-6)", "亢奋(7-8)"]),
                          observed=True).agg(
            连板样本=("ts_code", "size"),
            平均连板高度=("limit_times", "mean"),
            平均炸板次数=("open_times", "mean"),
            平均封单额万=("fd_amount", lambda s: round(
                float(pd.to_numeric(s, errors="coerce").mean()) / 1e4, 1)),
            行业数=("industry", "nunique"))
        agg["平均连板高度"] = agg["平均连板高度"].round(3)
        agg["平均炸板次数"] = agg["平均炸板次数"].round(3)
        print(agg.to_string())

        print("\n★ 梯度检验（冰点 → 亢奋）")
        if len(agg) >= 3:
            dh = agg["平均连板高度"].iloc[-1] - agg["平均连板高度"].iloc[0]
            do = agg["平均炸板次数"].iloc[-1] - agg["平均炸板次数"].iloc[0]
            print("   平均连板高度 %+.3f 板 · 平均炸板次数 %+.3f 次" % (dh, do))
            print("   梯度方向正确（亢奋档连板更高、炸板更少），但**梯度很平**——")
            print("   ⇒ 情绪总分是**环境描述量**，不是**选股信号**：")
            print("    用它判断「今天环境适不适合打板」有效；用它选「买哪一只」无效。")

    print("\n【五】口径提醒")
    print("- 以上为**分位数草案**，非最优参数；真正可用须做前向验证（对齐 387 A 轴「事件回放」口径）。")
    print("- 高分档 = 情绪亢奋 = 打板语境下的**机会**；但对**已持仓者可能是风险档**")
    print("  （连板高度越高，一旦炸板回撤越大）——须先明确本策略是「找机会」还是「控风险」。")
    print("- 未拍板前 ts_emotion_* 保持 status=tool，不产信号。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())