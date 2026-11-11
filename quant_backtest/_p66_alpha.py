# -*- coding: utf-8 -*-
"""P66 alpha/beta 分解：策略月收益对全市场等权月收益做回归。

目的：把「策略亏钱」拆成两部分——跟着市场跌（beta）与跑输市场（alpha）。
若 beta 显著 >1 且 alpha <=0，则策略本质是「高 beta 全市场」，无独立选股能力，
任何仓位/阈值/权重调整都只是在调 beta 敞口的大小。
"""
import json

SRC = r"C:/项目/老田的AI助手/quant_backtest/reports/_p66_env_continuous.json"
d = json.load(open(SRC, encoding="utf-8"))
rows = [x for x in d["gate_ab"]["monthly"]["rows"] if x["cfg"] == "base_w_cur_gate_on"]
rows = [r for r in rows if r.get("t5") is not None and r.get("bench_t5") is not None]
rows.sort(key=lambda r: r["month"])

xs = [r["bench_t5"] for r in rows]
ys = [r["t5"] for r in rows]
n = len(xs)
mx = sum(xs) / n
my = sum(ys) / n
sxx = sum((x - mx) ** 2 for x in xs)
sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
syy = sum((y - my) ** 2 for y in ys)
beta = sxy / sxx
alpha = my - beta * mx
r2 = (sxy ** 2) / (sxx * syy) if sxx * syy > 0 else 0.0
resid = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
se = (sum(e ** 2 for e in resid) / max(1, n - 2)) ** 0.5
se_beta = se / (sxx ** 0.5) if sxx > 0 else float("nan")
se_alpha = se * (1.0 / n + mx ** 2 / sxx) ** 0.5 if sxx > 0 else float("nan")

print("回归：策略月收益 = alpha + beta × 全市场等权月收益")
print("样本 %d 个有效月  %s ~ %s" % (n, rows[0]["month"], rows[-1]["month"]))
print()
print("  beta   = %+.4f   (标准误 %.4f, t=%.2f)" % (beta, se_beta, beta / se_beta if se_beta else 0))
print("  alpha  = %+.4f %% / 月  (标准误 %.4f, t=%.2f)" % (alpha * 100, se_alpha * 100,
                                                          alpha / se_alpha if se_alpha else 0))
print("  R^2    = %.3f" % r2)
print("  年化 alpha ≈ %+.2f %%" % (((1 + alpha) ** 12 - 1) * 100))
print()
print("解读：beta=%.2f 意味着策略波动约为全市场的 %.0f%%；" % (beta, beta * 100))
print("      alpha=%+.2f%%/月意味着扣掉市场因素后每月少赚这么多。" % (alpha * 100))

print()
print("=" * 70)
print("月度明细（%）")
print("=" * 70)
print("%-8s %10s %10s %10s %10s" % ("月份", "策略", "基准", "超额", "beta预测"))
for r, x, y in zip(rows, xs, ys):
    pred = alpha + beta * x
    print("%-8s %10.3f %10.3f %10.3f %10.3f" % (r["month"], y * 100, x * 100, (y - x) * 100, pred * 100))

print()
pos = [r for r, x in zip(rows, xs) if x > 0]
neg = [r for r, x in zip(rows, xs) if x <= 0]
print("按基准涨跌分组（检验「上涨月超额正、下跌月超额负」的 beta 特征）")
for tag, grp in (("基准上涨月", pos), ("基准下跌月", neg)):
    if not grp:
        continue
    ex = [(r["t5"] - r["bench_t5"]) for r in grp]
    print("  %s %d 个：月均超额 %+.3f%%，%d 正 / %d 负" % (
        tag, len(grp), sum(ex) / len(ex) * 100,
        sum(1 for e in ex if e > 0), sum(1 for e in ex if e <= 0)))
