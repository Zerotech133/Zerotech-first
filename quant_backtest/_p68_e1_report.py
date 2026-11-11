# -*- coding: utf-8 -*-
"""P68 E1 分析：maxret20 排除项到底改了什么。

三层取证：
  L1 全窗口 / 逐月 / IS-OOS —— 是否只是某月或某段贡献
  L2 各变体对市场月收益的 β —— 排除项是不是单纯在降敞口（关键判据）
  L3 同笔数对齐 —— 与「纯 score 提阈到同样笔数」比，剥离变相加严
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, "reports", "_p68_maxret_e1c.json")
d = json.load(open(PATH, encoding="utf-8"))
m = d["maxret_ab"]
bd = d["bench_daily"]

# ---- 市场月收益（复利）----
mk = pd.Series(bd["market"]["values"], index=[int(x) for x in bd["market"]["dates"]])
mret = mk.groupby([str(i)[:6] for i in mk.index]).apply(lambda s: float((1 + s).prod() - 1))

VARS = ["base", "ex_new", "ex_p80", "ex_p70", "ex_abs10", "ex_abs07", "ex_abs05"]


def ols(y, x):
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    mm = np.isfinite(x) & np.isfinite(y)
    y, x = y[mm], x[mm]
    n = len(x)
    if n < 6:
        return None
    X = np.column_stack([np.ones(n), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    s2 = float(resid @ resid) / (n - 2)
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return {"a": float(b[0]), "b": float(b[1]), "t": float(b[1] / se[1]),
            "ta": float(b[0] / se[0]), "n": n}


print("=" * 80)
print("E1 maxret20 排除项　报告：%s" % os.path.basename(PATH))
print("=" * 80)

print()
print("L1  全窗口")
print("%-9s %8s %10s %10s %10s  %s" % ("cfg", "n_buy", "T+5", "T+10", "T+20", "说明"))
for k in VARS:
    v = m["bands"][k]
    print("%-9s %8d %9.4f%% %9.4f%% %9.4f%%  %s" % (
        k, v["n_buy"], v["t5"] * 100, v["t10"] * 100, v["t20"] * 100, v["desc"]))

print()
print("     买入样本内分位分档（单调才说明排除有效）")
for k in ("q1", "q2", "q3", "q4", "q5", "q_nan"):
    if k not in m["bands"]:
        continue
    v = m["bands"][k]
    print("%-9s %8d %9.4f%% %9.4f%% %9.4f%%  %s" % (
        k, v["n_buy"], v["t5"] * 100, v["t10"] * 100, v["t20"] * 100, v["desc"]))

# ---- L2：每个变体对市场月收益的 β ----
print()
print("L2  各变体对市场月收益的回归（T+5 月度序列）")
print("%-9s %6s %10s %8s %8s   %s" % ("cfg", "n月", "β", "t", "α/月", "判定"))
betas = {}
for k in VARS:
    if k not in m["monthly"]:
        continue
    rows = {r["month"]: r for r in m["monthly"][k]}
    pts = [(mo, r["t5"]) for mo, r in rows.items()
           if r.get("t5") is not None and mo in mret.index]
    if len(pts) < 6:
        continue
    y = [p[1] for p in pts]
    x = [float(mret[mo]) for mo, _ in pts]
    r = ols(y, x)
    if not r:
        continue
    betas[k] = r
    tag = "高敞口" if r["b"] > 1.15 else ("接近市场" if r["b"] > 0.85 else "低敞口")
    print("%-9s %6d %9.3f %8.2f %7.3f%%   %s" % (
        k, r["n"], r["b"], r["t"], r["a"] * 100, tag))

# ---- L3：同笔数对齐 ----
print()
print("L3  同笔数对齐：排除档 vs 纯 score 提阈到相同笔数")
scan = m["th_scan"]


def nearest(n_target):
    best = min(scan, key=lambda r: abs(r["n_buy"] - n_target))
    return best


print("%-9s %8s %10s %10s ｜ %-9s %8s %10s %10s ｜ %s" % (
    "排除档", "笔数", "T+5", "T+10", "同笔数阈值", "笔数", "T+5", "T+10", "差(T+5)"))
for k in ("ex_p80", "ex_p70", "ex_abs10", "ex_abs07", "ex_abs05"):
    v = m["bands"][k]
    ref = nearest(v["n_buy"])
    diff = v["t5"] - ref["t5"]
    print("%-9s %8d %9.4f%% %9.4f%% ｜ th=%-6.1f %8d %9.4f%% %9.4f%% ｜ %+.4f%%" % (
        k, v["n_buy"], v["t5"] * 100, v["t10"] * 100, ref["buy_th"],
        ref["n_buy"], ref["t5"] * 100, ref["t10"] * 100, diff * 100))

# ---- 逐月 ----
print()
print("逐月 T+5（%）")
mo_keys = sorted({r["month"] for r in m["monthly"]["base"]})
bym = {k: {r["month"]: r for r in m["monthly"][k]} for k in m["monthly"]}
show = [k for k in ("base", "ex_p80", "ex_abs10") if k in bym]
print("%-8s" % "month" + "".join("%16s" % k for k in show))
for mo in mo_keys:
    line = "%-8s" % mo
    for k in show:
        r = bym[k].get(mo, {})
        v = r.get("t5")
        line += "%16s" % ("—" if v is None else "%+.3f%%(%d)" % (v * 100, r["n_buy"]))
    print(line)

# ---- 分段 ----
print()
half = len(mo_keys) // 2
segs = {"IS": mo_keys[:half], "OOS": mo_keys[half:]}


def seg(k, ms, h):
    v = [bym[k][mo][h] for mo in ms
         if bym[k].get(mo, {}).get(h) is not None]
    return sum(v) / len(v) if v else None


print("分段等权（跳过无信号月）")
for tag, ms in segs.items():
    print("  %s %s~%s" % (tag, ms[0], ms[-1]))
    for k in ("base", "ex_p80", "ex_abs10"):
        if k not in bym:
            continue
        a, b = seg(k, ms, "t5"), seg(k, ms, "t10")
        print("    %-9s T+5 %+8.4f%%  T+10 %+8.4f%%" % (
            k, (a or float("nan")) * 100, (b or float("nan")) * 100))

print()
print("读法：若 β 随排除加深单调下降，则「排除项」只是降敞口的另一种写法，")
print("      不是选股能力；此时用减仓降敞口更优（不损失选股信息）。")
