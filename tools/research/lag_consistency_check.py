# -*- coding: utf-8 -*-
"""lag 口径一致性回归检查：fast 与 daily 两个实现必须给出同一个 lag。

为什么需要这个脚本
------------------
扩展池的 `lag`（信号结构端点到最早可见确认日的 K 线数）有两个实现：

- `_find_lag`（fast 模式）：全窗出候选 → 对每个信号从 i0+1 起逐根重放定 lag
- `_rows_daily`（daily 模式）：从 MIN_BARS 起逐日重放，完全复刻产线每日扫描

两者在 2026-11-08 之前**不一致**，且原因不止一个：
  1. `--since` 按字符串比较（`"2023-09-22" < "20231001"` 因为 `'-'` < `'0'`），
     2023 的确认日被静默丢弃；daily 模式下这会把信号推迟到 2024-01-02，
     lag 虚高（实测旧池该日堆了 2,826 笔）。
  2. `_find_lag` 缺 `MIN_BARS` 下限，能在窗口才几十根时就判定「可见」，
     而产线 `if len(bars) < MIN_BARS: return` 根本不扫这种票 → 造出产线
     永远执行不到入场（污染 4.10%）。

修完后两口径应当逐信号一致。**本脚本就是这个断言的回归测试**：任何对
`_find_lag` / `_seg` / `_rows_daily` 的改动，跑一遍，一致率必须是 100%。

用法
----
    python lag_consistency_check.py            # 抽 20 只票
    python lag_consistency_check.py --n 60     # 抽 60 只
    python lag_consistency_check.py --since 20240102
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.join(os.path.dirname(os.path.dirname(HERE)), "quant_backtest")
if QB not in sys.path:
    sys.path.insert(0, QB)

ASHARE = os.path.join(QB, "data", "ashare")
MIN_BARS = 120
WIN = 500
MAX_LAG = 60


def main():
    ap = argparse.ArgumentParser(description="fast/daily lag 一致性回归检查")
    ap.add_argument("--n", type=int, default=20, help="抽样股票数（--n 0 = 全跑，很慢）")
    ap.add_argument("--since", default="20230701", help="确认日下限（与回填口径一致）")
    a = ap.parse_args()

    import chanlun_scan as cs
    from chanlun_ext_backfill import _find_lag, _rows_daily
    from chanlun_signal import analyze

    files = sorted(f for f in os.listdir(ASHARE) if f.endswith(".parquet"))
    files = [f for f in files if f[:-8] != "000001_SH"]
    if a.n and a.n < len(files):
        step = max(1, len(files) // a.n)
        files = files[::step][:a.n]

    tot = same = 0
    diffs = []
    skipped = 0
    for fn in files:
        bars = cs._bars_from_parquet(os.path.join(ASHARE, fn))
        if not bars or len(bars) < MIN_BARS:
            skipped += 1
            continue
        code = fn[:-8].replace("_", ".")
        daily = {(r["type"], r["dt"]): r["lag"]
                 for r in _rows_daily(bars, WIN, a.since, MAX_LAG, fn)}
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        cache = {}
        for s in analyze(bars)["signals"]:
            i0 = idx.get(s["dt"])
            if i0 is None:
                continue
            k = (s["type"], s["dt"])
            if k not in daily:
                continue  # 全窗候选在尾部窗不可见 / 超 max_lag，daily 本就不记
            lag = _find_lag(bars, i0, s, WIN, MAX_LAG, cache)
            tot += 1
            if lag == daily[k]:
                same += 1
            elif len(diffs) < 20:
                diffs.append((code, k[0], k[1], i0, len(bars), daily[k], lag))
        print("  %-10s bars=%4d daily_sig=%3d  (累计一致 %d/%d)"
              % (code, len(bars), len(daily), same, tot), flush=True)

    print()
    print("样本 %d 只（跳过不足 %d 根的 %d 只）" % (len(files), MIN_BARS, skipped))
    if tot == 0:
        print("没有可比信号 —— 检查 --since 是否早于数据起点")
        return 2
    rate = 100.0 * same / tot
    print("逐信号对比：%d 条，lag 一致 %d，不一致 %d（一致率 %.2f%%）"
          % (tot, same, tot - same, rate))
    if diffs:
        print("不一致样例：")
        for c, t, dt, i0, nb, d, f in diffs:
            print("   %s %s %s  i0=%3d bars=%4d  daily=%s  fast=%s"
                  % (c, t, dt, i0, nb, d, f))
        print("→ 两口径已不一致，说明 lag 计算被改坏，或数据被重灌。先查 MIN_BARS 下限。")
        return 1
    print("→ PASS：fast 与 daily 完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
