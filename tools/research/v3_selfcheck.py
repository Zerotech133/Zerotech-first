# -*- coding: utf-8 -*-
"""three_layer_v3 / hold_term_* 的自检（K-RES-20261108-V3-CHECK）。

四条断言，每条都指向一个可能出错的地方：

A1 复算一致   用价格面板重算 20 日净收益，必须与台账 hold_ret 逐笔一致。
              不一致说明价格面板口径（复权/成本）接错了，后面所有数字全部作废。
A2 复现基线   全信号 + 持 20 日 + 无单票上限，必须复现 ~-5%（与
              combo_shadow_stats 的「全信号 + 持满20」同向同量级）。
A3 置换检验   把「强/弱」标签对调后重跑 D 配置，收益必须**显著恶化**。
              若不恶化，说明势标签没起作用，D 的正收益来自别处（如单纯少做了
              一半笔数），那"择势"结论就是假的。
A4 归零检验   把持有期强制回 20 日，C 配置必须回到负值。
              若仍为正，说明 C 的正收益不是持有期给的，整条推理链断掉。

任一条失败 exit 1，并打印具体是哪条。

用法:
    python tools/research/v3_selfcheck.py
"""

from __future__ import annotations

import glob
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
IDX_CODE = "000985.CSI"
TH = 0.02
BUY, SELL = 0.00075, 0.00125

fails = []


def check(name, ok, detail):
    print(("  PASS  " if ok else "  FAIL  ") + name + "  " + detail)
    if not ok:
        fails.append(name)


def net_of(c0, c1):
    return (c1 * (1.0 - SELL)) / (c0 * (1.0 + BUY)) - 1.0


def main():
    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass") and r.get("mature")]
    P = load_price_panel(sorted({r["code"] for r in passed}), TDX)

    # ── A1 复算一致 ──
    diffs = []
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None:
            continue
        j = min(i + 20, len(b["d"]) - 1)
        if j <= i:
            continue
        diffs.append(abs(net_of(b["c"][i], b["c"][j]) - r["hold_ret"]))
    mx = max(diffs) if diffs else 1.0
    check("A1 复算一致", mx < 1e-4,
          "829 笔 20 日净收益 vs 台账 hold_ret，max|diff|=%.2e" % mx)

    # 准备日历与势标签
    import pandas as pd
    clos = {}
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == IDX_CODE]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                clos[dt.replace("-", "")[:8]] = c
    clos = dict(sorted(clos.items()))
    ds = list(clos)
    mom = {ds[i]: clos[ds[i]] / clos[ds[i - 20]] - 1.0 for i in range(20, len(ds))}
    d_lo = min(r["entry_date"] for r in passed)
    axis = [d for d in clos if d >= d_lo]
    apx = {d: i for i, d in enumerate(axis)}

    def label(d, swap=False):
        m = mom.get(d)
        if m is None:
            return None
        lab = "弱" if m < -TH else ("强" if m > TH else "中")
        if swap and lab != "中":
            lab = "强" if lab == "弱" else "弱"
        return lab

    rows = []
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None or r["entry_date"] not in apx:
            continue
        rows.append({"r": r, "b": b, "i": i})

    def run(types=None, h_of=None, skip_weak=False, swap=False, cap=0.05):
        by = defaultdict(list)
        for x in rows:
            r, b, i = x["r"], x["b"], x["i"]
            if types and r["type"] not in types:
                continue
            lab = label(r["entry_date"], swap)
            if h_of:
                if lab is None:
                    continue
                if skip_weak and lab == "弱":
                    continue
                h = h_of.get(lab)
                if h is None:
                    continue
            else:
                h = 20
            j = min(i + h, len(b["d"]) - 1)
            if j <= i:
                continue
            c0, c1 = b["c"][i], b["c"][j]
            ret = net_of(c0, c1)
            exit_px = (1.0 + ret) * c0 * (1 + BUY) / (1 - SELL)
            ratio = build_ratio(b, i, j, c0, exit_px, axis, apx)
            by[r["entry_date"]].append(
                Pick(r["code"], apx[r["entry_date"]], apx[b["d"][j]], ratio, ret))
        return Engine(axis, hold=20 if not h_of else min(h_of.values()))\
            .run(by, per_name_cap=cap)

    # ── A2 复现基线 ──
    base = run(cap=None)
    check("A2 复现基线", -12.0 < base["total"] < 0.0,
          "全信号+持20 累计 %+.2f%%（应落在 -12 到 0 之间，实测约 -5）" % base["total"])

    # ── A3 置换：强/弱对调后 D 必须恶化 ──
    d_normal = run(types=("1买",), h_of={"强": 5, "中": 5}, skip_weak=True)
    d_swap = run(types=("1买",), h_of={"强": 5, "中": 5}, skip_weak=True, swap=True)
    check("A3 置换检验", d_swap["total"] < d_normal["total"] - 1.0,
          "正常 %+.2f%%  对调 %+.2f%%  差 %+.2fpp（应至少差 1pp）"
          % (d_normal["total"], d_swap["total"], d_normal["total"] - d_swap["total"]))

    # ── A4 归零：持有期回 20，C 必须变负 ──
    c5 = run(types=("1买",), h_of={"强": 5, "中": 5, "弱": 5})
    c20 = run(types=("1买",), h_of={"强": 20, "中": 20, "弱": 20})
    check("A4 归零检验", c20["total"] < 0 < c5["total"],
          "1买持5 %+.2f%%  1买持20 %+.2f%%（持5须正、持20须负）"
          % (c5["total"], c20["total"]))

    print("\n%d/%d 通过" % (4 - len(fails), 4))
    if fails:
        print("失败项：" + "、".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
