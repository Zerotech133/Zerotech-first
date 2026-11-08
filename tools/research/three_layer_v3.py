# -*- coding: utf-8 -*-
"""三层框架 v3 的账户级落地：把「选择 → 择势 → 择时」装成可执行的五档配置（K-RES-20261108-V3）。

前三个脚本各自证了一件事
------------------------
`crowd_gate.py`          拥挤度当势：无增量（b_logN t=−0.43），6 个月里 4 个月更差 → 否
`hold_term_lab.py`       只换持有期：20 日 −1.20% → 3 日 +1.45% → 持有期是主变量
`regime_term_interaction.py`
    按笔等权（不受日批加权影响）重算势 × 期限矩阵，符号彻底翻转：
      强市 h=5  +1.08%（t=2.49）   h=8  +1.73%（t=3.17）
      弱市 h=5  −0.90%（t=−2.42）  h=20 −5.57%（t=−7.31）
    上一轮「越弱越涨」是**日批加权**造出来的：弱市恐慌日信号爆炸，日批等权把
    它们的权重压低，账面就好看了；按笔算，弱市每一笔都在亏。

于是三层各自有了明确职责：
    L1 选择   1买 > 2买（2买 在任何持有期都是负的）
    L2 择势   势不是开关，是**准入门槛**：弱市不做
    L3 择时   不是找入场价，是**定持有期**：强市 5~8 日、中市 1 日

本脚本把这套规则装成配置，用同一个组合引擎、同一批信号跑到底，比较五档：
    A 现行      全信号 + 固定 20 日 + 无单票上限
    B A+上限    A 加单票 5% 上限（检验 A 是否靠重仓）
    C 只改交易  1买 + 固定 5 日 + 上限（不动择势，看只改持有期能拿多少）
    D C+择势    1买 + 弱市不做 + 强市 5 日/中市 5 日 + 上限
    E 全规则    1买 + 弱市不做 + 强市 8 日/中市 1 日 + 上限

所有配置都报 util：util 差 2 倍的 cagr 不可横向比。

用法:
    python tools/research/three_layer_v3.py
    python tools/research/three_layer_v3.py --json
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
OUT = os.path.join(ROOT, "portal", "three_layer_v3.json")

IDX_CODE = "000985.CSI"
TH = 0.02
BUY, SELL = 0.00075, 0.00125


def load_axis_and_mom(n=20):
    import pandas as pd
    clos = {}
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == IDX_CODE]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                clos[dt.replace("-", "")[:8]] = c
    clos = dict(sorted(clos.items()))
    axis = sorted(clos)
    ds = list(clos)
    mom = {ds[i]: clos[ds[i]] / clos[ds[i - n]] - 1.0 for i in range(n, len(ds))}
    return axis, mom


def net_of(c0, c1):
    return (c1 * (1.0 - SELL)) / (c0 * (1.0 + BUY)) - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass") and r.get("mature")]
    P = load_price_panel(sorted({r["code"] for r in passed}), TDX)
    axis, mom = load_axis_and_mom()
    apx = {d: i for i, d in enumerate(axis)}
    d_lo = min(r["entry_date"] for r in passed)
    # 上界不能截到最后一个开仓日：持有 20 日的笔会跨过它，exit 日不在轴上会 KeyError。
    # 轴一直延到数据末日，净值曲线尾部多出来的天数只影响 cagr 的年限分母，不影响笔收益。
    axis = [d for d in axis if d >= d_lo]
    apx = {d: i for i, d in enumerate(axis)}

    def reg(d):
        m = mom.get(d)
        if m is None:
            return None
        return "弱" if m < -TH else ("强" if m > TH else "中")

    # 先给每笔打上势标签与自身下标
    rows = []
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None or r["entry_date"] not in apx:
            continue
        rows.append({"r": r, "b": b, "i": i, "reg": reg(r["entry_date"])})
    say("可用 %d 笔｜日历 %d 日｜势分档：弱 %d / 中 %d / 强 %d 天"
        % (len(rows), len(axis),
           len({x["r"]["entry_date"] for x in rows if x["reg"] == "弱"}),
           len({x["r"]["entry_date"] for x in rows if x["reg"] == "中"}),
           len({x["r"]["entry_date"] for x in rows if x["reg"] == "强"})))

    def build(types=None, h_of=None, skip_weak=False, skip_mid=False):
        """h_of: 由势决定持有期；skip_weak/skip_mid: 势准入。"""
        by = defaultdict(list)
        for x in rows:
            r, b, i = x["r"], x["b"], x["i"]
            if types and r["type"] not in types:
                continue
            if skip_weak and x["reg"] == "弱":
                continue
            if skip_mid and x["reg"] == "中":
                continue
            h = 20
            if h_of:
                h = h_of.get(x["reg"])
                if h is None:
                    continue
            j = min(i + h, len(b["d"]) - 1)
            if j <= i:
                continue
            c0, c1 = b["c"][i], b["c"][j]
            ret = net_of(c0, c1)
            exit_px = (1.0 + ret) * c0 * (1 + BUY) / (1 - SELL)
            ratio = build_ratio(b, i, j, c0, exit_px, axis, apx)
            by[r["entry_date"]].append(
                Pick(r["code"], apx[r["entry_date"]], apx[b["d"][j]], ratio, ret))
        return by

    configs = [
        ("A 现行", build(), 20, None),
        ("B A+单票5%", build(), 20, 0.05),
        ("C 只改交易", build(types=("1买",), h_of={"强": 5, "中": 5, "弱": 5}), 5, 0.05),
        ("D C+弱市不做", build(types=("1买",), h_of={"强": 5, "中": 5}, skip_weak=True), 5, 0.05),
        ("E 强8/中1", build(types=("1买",), h_of={"强": 8, "中": 1}, skip_weak=True), 8, 0.05),
        ("F 只1买+持5", build(types=("1买",), h_of={"强": 3, "中": 3}, skip_weak=True), 3, 0.05),
    ]

    say("\n同一引擎、同一批信号，五档配置（成本 20bp，单票上限 5%）")
    say("  %-14s %6s %9s %8s %8s %7s %7s" %
        ("配置", "笔数", "累计", "cagr", "mdd", "sharpe", "util"))
    res = {}
    for lab, picks, hold, cap in configs:
        nb = sum(len(v) for v in picks.values())
        v = Engine(axis, hold=hold).run(picks, per_name_cap=cap)
        calmar = (v["cagr"] / abs(v["mdd"])) if (v["cagr"] and v["mdd"]) else None
        res[lab] = {k: v[k] for k in ("total", "cagr", "mdd", "sharpe", "util")}
        res[lab].update({"n_picks": nb, "n_days": len(picks), "curve": v.get("curve"),
                         "calmar": round(calmar, 2) if calmar else None})
        say("  %-14s %6d %+8.2f%% %+7.2f%% %+7.2f%% %+7.2f %6.1f%%"
            % (lab, nb, v["total"], v["cagr"] or 0, v["mdd"], v["sharpe"], v["util"]))

    # 逐月净值：看 C/D 的收益是否集中在一个月
    say("\n逐月末累计净值（相对期初）")
    for lab, picks, hold, cap in configs[:5]:
        v = Engine(axis, hold=hold).run(picks, per_name_cap=cap)
        cur = v["curve"]
        if not cur:
            continue
        m_e = {}
        for idx, d in enumerate(axis):
            m_e[d[:6]] = cur[idx]
        row = " ".join("%s:%+.1f%%" % (k[4:], (val - 1) * 100) for k, val in sorted(m_e.items()))
        say("  %-14s %s" % (lab, row))
        res[lab]["month_equity"] = {k: round((val - 1) * 100, 2) for k, val in sorted(m_e.items())}

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"axis": axis, "configs": res}, f, ensure_ascii=False, indent=1)
    if not a.json:
        print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
