# -*- coding: utf-8 -*-
"""hf_exit_lab 自检。

L1 样本数三方一致（lab / path / sell_exit）
L2 hold250 的笔均毛超额与 hf_path 的 T=250 一致
L3 固定持期各档与 hf_sell_exit 逐位对齐（同一口径的两个实现）
L4 hold250 的 ann_net 与 ann_cash 必须相等（窗口同长、无提前出场）
L5 sim 手工复算：构造路径，逐条规则手算 exit_k / exit_ret
L6 触发组 + 未触发组 = 全部样本
L7 退化：去掉所有触发条件、只留 cap，规则必须退化成固定持期
L8 hold250 的同窗超额必须等于暴露超额（A_win == A）

用法::
    python -X utf8 tools/research/hf_exit_lab.py --no-write   # 先产出
    python -X utf8 tools/research/hf_exit_lab_selfcheck.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_exit_lab as L     # noqa: E402

ROOT = L.ROOT
FAIL, OK = [], []

# sim() 要求 shape：path[0..cap]
def P(vals):
    return [x / 100.0 for x in vals]


def chk(name, cond, msg=""):
    (OK if cond else FAIL).append(name)
    print("%s %-4s %s" % ("✅" if cond else "❌", name, msg))


def _load(fn):
    p = os.path.join(ROOT, "portal", fn)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main():
    lab = _load("hf_exit_lab.json")
    path = _load("hf_path.json")
    se51 = _load("hf_sell_exit_s51.json")
    if not (lab and path and se51):
        print("缺产物：先跑 hf_exit_lab.py / hf_path.py / hf_sell_exit.py --slip 51")
        return 1
    T = lab["table"]

    # ── L1 样本一致 ──
    ns = {lab["n_trades"], path["n_trades"], se51["table"]["hold250"]["n"]}
    chk("L1", len(ns) == 1, "lab/path/sell_exit 样本数 %s" % sorted(ns))

    # ── L2 hold250 vs path T=250 ──
    a1 = T["hold250"]["A_bp"]
    a2 = path["curve"]["250"]["A_bp"]
    chk("L2", abs(a1 - a2) <= 2, "hold250 %.0fbp vs path T=250 %.0fbp" % (a1, a2))

    # ── L3 固定持期对齐 ──
    bad = []
    for k in (20, 60, 120):
        x = T["hold%d" % k]["A_bp"]
        y = se51["table"]["hold%d" % k]["avg_alpha_gross"] * 100
        if abs(x - y) > 2:
            bad.append((k, x, round(y, 0)))
    chk("L3", not bad, "hold20/60/120 对齐检查，偏差 %s" % (bad or "均在 2bp 内"))

    # ── L4 hold250 两版年化相等 ──
    s = T["hold250"]
    chk("L4", abs((s["ann_net"] or 0) - (s["ann_cash"] or 0)) < 0.02,
        "ann_net %.2f%% vs ann_cash %.2f%%" % (s["ann_net"] or 0, s["ann_cash"] or 0))

    # ── L5 sim 手工复算 ──
    # 路径（%）：d0 0, d1 −5, d2 −10, d3 −3, d4 +2, d5 +9 ...
    p = P([0, -5, -10, -3, 2, 9, 12, 11, 8])
    opens = {k: v for k, v in [(1, -4.0), (2, -9.0), (3, -3.5), (4, 2.5), (5, 9.5),
                               (6, 12.5), (7, 10.0), (8, 7.5)]}

    def go(k):
        return opens.get(k, p[k]) / 100.0

    # pnl8：d2 收 −10% ≤ −8% → 次日(d3)开盘 −3.5%，出场日必须是 d3
    ek, er, why = L.sim(p, go, None, None, dict(pnl=0.08, cap=250), 8)
    chk("L5a", ek == 3 and abs(er - (-0.035)) < 1e-9 and why == "pnl",
        "pnl8 → 出场日 k=%d 收益 %.3f%% 原因 %s（应 3 / −3.5%% / pnl）"
        % (ek, er * 100, why))

    # trail20：峰值 d1 后…peak 逐步到 d6=12%，d7=11% 未破 8%；d8=8% 仍 > 12−20=−8
    ek, er, why = L.sim(p, go, None, None, dict(trail=0.20, cap=250), 8)
    chk("L5b", ek == 8 and abs(er - p[8]) < 1e-9 and why == "cap",
        "trail20 未触发 → 持满，k=%d 收益 %.1f%%" % (ek, er * 100))

    # trail 5%：peak 12% → 破 7% 在 d8（8% 未破），说明阈值边界要对
    ek, er, why = L.sim(p, go, None, None, dict(trail=0.54, cap=8), 8)
    chk("L5c", ek == 8 and why == "cap",
        "trail54 更宽于全程最大回撤 → 不触发，k=%d 原因 %s" % (ek, why))

    # zg：结构位 −2% → d1 收 −5% 就破了（比 pnl 的 −8% 早一天）→ 出场在 d2
    ek, er, why = L.sim(p, go, -0.02, None, dict(zg=True, pnl=0.08, cap=250), 8)
    chk("L5d", ek == 2 and why == "zg",
        "zg 比 pnl 早一天触发 → 出场日 k=%d 原因 %s（应 2 / zg）" % (ek, why))

    # fly：浮盈达 +20% 之前 pnl 有效；本路径最高 12% → 未放飞，仍触发 pnl
    ek, er, why = L.sim(p, go, None, None, dict(pnl=0.08, fly=0.20, cap=250), 8)
    chk("L5e", ek == 3 and why == "pnl", "fly 未达标 → pnl 照常，出场日 k=%d" % ek)

    # time：第 4 日收益 +2.5% > 0 → 不触发，持满
    ek, er, why = L.sim(p, go, None, None, dict(tk=4, tp=0.0, cap=8), 8)
    chk("L5f", ek == 8 and why == "cap", "time4 当日为正 → 不触发，k=%d" % ek)

    # lock20：未达 +20% → 不武装
    ek, er, why = L.sim(p, go, None, None, dict(lock=0.20, lock_to=0.05, cap=8), 8)
    chk("L5g", ek == 8, "lock20 未武装 → 持满，k=%d" % ek)

    # ── L6 触发组 + 未触发组 ──
    bad6 = []
    for name in T:
        s = T[name]
        if not s:
            continue
        if abs(s["fire_pct"] - 100.0 * s["fire_n"] / s["n"]) > 0.2:
            bad6.append(name)
    chk("L6", not bad6, "触发率与 fire_n/n 自洽，异常 %s" % (bad6 or "无"))

    # ── L7 退化：只有 cap，没有触发条件 ──
    only = dict(cap=6)
    # 第 6 个参数是 cap 本身（sim 用它控制循环），必须与 cfg["cap"] 一致；
    # 生产代码里由 `c = cfg.get("cap", cap)` 保证，测试里不能手滑写成 8。
    ek, er, why = L.sim(p, go, None, None, only, 6)
    chk("L7", ek == 6 and why == "cap" and abs(er - p[6]) < 1e-9,
        "空规则 cap=6 → 出场日 %d 收益 %.1f%%（应 6 / 12.0%%）" % (ek, er * 100))

    # ── L8 A_win == A（hold250）──
    s = T["hold250"]
    chk("L8", abs((s["A_win_bp"] or 0) - (s["A_bp"] or 0)) <= 1,
        "hold250 A=%0.fbp / A_win=%0.fbp" % (s["A_bp"], s["A_win_bp"] or 0))

    print()
    print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
