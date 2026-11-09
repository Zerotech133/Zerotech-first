# -*- coding: utf-8 -*-
"""验证「势」档位是否真有预测力 —— 不是逻辑自洽就行，要看钱。

要回答的问题
------------
`chanlun_regime` 把市场分成 GREEN / AMBER / RED 三档，逻辑上是缠论的标准
推法（中枢上移+站上上沿=可做，中枢下移+跌破下沿=不做）。但一套规则逻辑
自洽、读起来漂亮，跟它能不能提前告诉你「接下来会跌」是两件事。

所以这里做最朴素的检验：历史上每一天的档位，对应之后 5/10/20 个交易日
的指数收益是多少。三档如果单调递增（RED < AMBER < GREEN），说明框架有
信息；如果三档收益差不多，说明它只是把「过去跌了」重新描述一遍。

一个必须承认的偏误
------------------
档位是从价格结构推出来的，而价格结构本身由过去的涨跌决定。所以这套
规则天然有「追涨杀跌」的动量属性，它在趋势市里会有效、在反转市里会
滞后。验证结果必须带上这一条解读，不能宣称「能预判」。

用法:
    python tools/research/regime_validation.py
    python tools/research/regime_validation.py --code 932000.CSI --lookback 400
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, "quant_backtest"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chanlun_regime as RG  # noqa: E402

HORIZONS = (5, 10, 20)


def build(code, asof, lookback):
    bars = RG._load_index(code, asof)
    if len(bars) < 200:
        raise SystemExit("行情不足")
    lo = max(140, len(bars) - lookback)
    recs = []
    for j in range(lo, len(bars)):
        st = RG.structure_state(bars[: j + 1])
        g, _ = RG.grade_one(st)
        recs.append({"i": j, "dt": bars[j]["dt"], "grade": g,
                     "px": bars[j]["c"], "zone": st["zone"] if st else None,
                     "pv_dir": st["pv_dir"] if st else None})
    return bars, recs


def forward_stats(bars, recs, h):
    """每个档位下，之后 h 个交易日的收益分布。"""
    by = {}
    for r in recs:
        j = r["i"]
        if j + h >= len(bars):
            continue
        fwd = bars[j + h]["c"] / bars[j]["c"] - 1
        by.setdefault(r["grade"], []).append(fwd)
    return by


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def trans_effect(bars, recs, h):
    """转档事件之后的表现。

    比「档位快照」更贴近使用场景：你不是每天都调仓，你是看到转档才动作。
    所以真正该验的是 GREEN→RED 之后会不会跌、RED→GREEN 之后会不会涨。
    """
    out = {}
    for k in range(1, len(recs)):
        a, b = recs[k - 1]["grade"], recs[k]["grade"]
        if a == b:
            continue
        j = recs[k]["i"]
        if j + h >= len(bars):
            continue
        fwd = bars[j + h]["c"] / bars[j]["c"] - 1
        out.setdefault("%s→%s" % (a, b), []).append(fwd)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000985.CSI")
    ap.add_argument("--asof", default="20261106")
    ap.add_argument("--lookback", type=int, default=400)
    args = ap.parse_args()

    asof = args.asof.replace("-", "")
    bars, recs = build(args.code, asof, args.lookback)
    print("=" * 76)
    print("势档位的预测力检验 · %s · %s ~ %s · %d 天"
          % (args.code, recs[0]["dt"], recs[-1]["dt"], len(recs)))
    print("=" * 76)

    print("\n[1] 按档位分组的后续收益（指数，未扣成本）")
    print("  %-6s %6s %9s %9s %9s %8s %8s"
          % ("档位", "天数", "5日均", "10日均", "20日均", "20日t", "20日胜率"))
    tbl = {}
    for g in ("GREEN", "AMBER", "RED"):
        row = {}
        for h in HORIZONS:
            v = forward_stats(bars, recs, h).get(g, [])
            row[h] = (len(v), float(np.mean(v)) * 100 if v else None,
                      (float(np.mean(np.array(v) > 0)) * 100) if v else None)
        v20 = forward_stats(bars, recs, 20).get(g, [])
        tbl[g] = row
        print("  %-6s %6d %8s%% %8s%% %8s%% %8.2f %7s%%"
              % (g, row[5][0],
                 ("%.2f" % row[5][1]) if row[5][1] is not None else "-",
                 ("%.2f" % row[10][1]) if row[10][1] is not None else "-",
                 ("%.2f" % row[20][1]) if row[20][1] is not None else "-",
                 tstat(v20), ("%.1f" % row[20][2]) if row[20][2] is not None else "-"))

    print("\n[2] 单调性检查")
    m5, m10, m20 = [], [], []
    for g in ("RED", "AMBER", "GREEN"):
        for arr, h in ((m5, 5), (m10, 10), (m20, 20)):
            v = tbl[g][h][1]
            arr.append(v if v is not None else np.nan)
    ok = True
    for lab, arr in (("5日", m5), ("10日", m10), ("20日", m20)):
        mono = all(a < b for a, b in zip(arr[:-1], arr[1:])
                   if a == a and b == b)
        print("  %-4s RED %.2f%% → AMBER %.2f%% → GREEN %.2f%%  %s"
              % (lab, arr[0], arr[1], arr[2],
                 "单调递增 ✓" if mono else "**不单调**"))
        ok = ok and mono
    print("  结论：%s" % ("三档收益随档位单调，框架有信息量"
                         if ok else "存在非单调，框架不能直接当择势用"))

    print("\n[3] 转档事件之后 20 日（更贴近实际操作）")
    tr = trans_effect(bars, recs, 20)
    for k in sorted(tr):
        v = np.asarray(tr[k], float)
        if len(v) < 3:
            continue
        print("  %-12s %3d 次  20日均 %+7.2f%%  中位 %+7.2f%%  t=%+5.2f"
              % (k, len(v), v.mean() * 100, np.median(v) * 100, tstat(v)))

    print("\n[4] 与「无脑持有」对比")
    all20 = []
    for r in recs:
        j = r["i"]
        if j + 20 < len(bars):
            all20.append(bars[j + 20]["c"] / bars[j]["c"] - 1)
    all20 = np.asarray(all20, float)
    print("  全样本任意日买入持有 20 日：均 %+.2f%%（n=%d）"
          % (all20.mean() * 100, len(all20)))
    for g in ("GREEN", "AMBER", "RED"):
        v = forward_stats(bars, recs, 20).get(g, [])
        if v:
            print("  %-6s 买入：均 %+.2f%%，相对全样本 %+.2fpp"
                  % (g, np.mean(v) * 100,
                     (np.mean(v) - all20.mean()) * 100))

    print("\n[5] 必须声明的偏误")
    print("  档位由价格结构推出，结构由过去涨跌决定 → 规则自带动量属性。")
    print("  在趋势市有效、在反转市滞后（V 型反转时会在底部区一直 RED）。")
    print("  它回答的是「现在顺势还是逆势」，不是「明天涨还是跌」。")
    print("=" * 76)


if __name__ == "__main__":
    main()
