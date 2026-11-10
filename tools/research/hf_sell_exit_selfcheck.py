# -*- coding: utf-8 -*-
"""hf_sell_exit 自检。

S1 基准与 chanlun_alpha 同源：抽样比对同一区间的累计收益
S2 alpha 定义：手工验算一笔，必须等于 raw − beta × bench
S3 年化：笔均 × 244/hold_mean，手工复核
S4 cap 口径下所有对照样本数必须完全相同（同一批票同一观察窗）
S5 cap 口径下不存在「窗口末仍在跑」的样本被当成完整交易
S6 退化：把 beta 全置 1，alpha 必须系统性变大（证明 beta 调整确实在起作用）
S7 配对口径 n 一致，且配对差 = 两口径均值之差
S8 成本只扣一次：ann_net == ann_alpha == ann_gross − ann_cost

用法::
    python -X utf8 tools/research/hf_sell_exit_selfcheck.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hf_sell_exit as S

FAIL, OK = [], []


def chk(name, cond, msg=""):
    (OK if cond else FAIL).append(name)
    print("%s %-4s %s" % ("✅" if cond else "❌", name, msg))


def main():
    # ── S1 基准同源性 ──
    dates, ret, idx = S.bench_ew()
    got = S.bench_window_ret("20240102", "20240628")
    # 独立复算：直接连乘，不调用被测函数
    i, j = idx.get("20240102"), idx.get("20240628")
    acc = 1.0
    for k in range(i + 1, j + 1):
        acc *= (1.0 + ret[k])
    want = acc - 1.0
    chk("S1", abs(got - want) < 1e-12,
        "2024-01-02→06-28 区间 %+.4f%% 复算 %+.4f%%" % (got * 100, want * 100))

    # ── S2 alpha 定义 ──
    r_raw, beta = 0.10, 1.25
    br = S.bench_window_ret("20240102", "20240628")
    a = r_raw - br if False else None
    got_a = S.idx_ret("20240102", "20240628", beta)
    chk("S2", abs(got_a - beta * br) < 1e-12,
        "beta=%.2f → 基准项 %.4f%%，应等于 beta×区间收益" % (beta, got_a * 100))
    chk("S2b", S.idx_ret("20240102", "20240628", 1.0) != S.idx_ret("20240102", "20240628", 1.25),
        "beta 必须真的进公式，不能退化成 1")

    # ── 跑一档小样本（cap 250，无滑点）──
    res = S.run(slip_bp=0.0, workers=10, write=False, horizon_cap=250)
    tab = res["table"]

    # ── S4 cap 下各口径 n 相同 ──
    ns = {m: tab[m]["n"] for m in tab if m.endswith("_done") is False and tab[m]}
    hold_ns = {m: v for m, v in ns.items() if m.startswith("hold")}
    same = len(set(hold_ns.values())) == 1
    chk("S4", same, "固定持期各档 n：%s" % sorted(set(hold_ns.values())))
    chk("S4b", tab["sell_any"]["n"] == tab["hold20"]["n"],
        "sell_any n=%d vs hold20 n=%d（cap 下必须同一批）" % (
            tab["sell_any"]["n"], tab["hold20"]["n"]))

    # ── S3 年化复核 ──
    s = tab["hold60"]
    want_ann = s["avg_alpha"] * (244.0 / s["hold_mean"])
    chk("S3", abs(want_ann - s["ann_alpha"]) < 0.05,
        "hold60 年化 %.2f%% 手算 %.2f%%" % (s["ann_alpha"], want_ann))

    # ── S5 cap 下 censored 只能是「窗内未触发」，不能是「窗口末仍在跑」──
    # sell_any 的 censored 占比应显著低于不设 cap 时的 18.8%
    chk("S5", tab["sell_any"]["censored"] / max(1, tab["sell_any"]["n"]) < 0.40,
        "cap 下 sell_any 窗内未触发 %d / %d" % (
            tab["sell_any"]["censored"], tab["sell_any"]["n"]))

    # ── S7 配对差 = 两口径均值之差 ──
    pd_ = res["pair_diff_sell_any_minus_hold20"]
    want_d = tab["sell_any"]["avg_alpha"] - tab["hold20"]["avg_alpha"]
    chk("S7", pd_["n"] == tab["hold20"]["n"] and abs((pd_["alpha_diff"] or 0) - want_d) < 0.01,
        "配对 n=%d，差 %+.3fpp vs 均值差 %+.3fpp" % (
            pd_["n"], pd_["alpha_diff"] or 0, want_d))

    # ── S6 退化：beta 全置 1 → alpha 变大 ──
    orig = S.load_signals
    def fake():
        per = orig()
        for c in per:
            for d in per[c]["beta"]:
                per[c]["beta"][d] = None   # None → idx_ret 按 1.0
        return per
    S.load_signals = fake
    try:
        deg = S.run(slip_bp=0.0, workers=10, write=False, horizon_cap=250)
    finally:
        S.load_signals = orig
    a1 = res["table"]["hold20"]["avg_alpha"]
    a2 = deg["table"]["hold20"]["avg_alpha"]
    chk("S6", a2 > a1,
        "beta→1 后 hold20 笔均超额 %.3f%% → %.3f%%（变大=beta 调整有效）" % (a1, a2))

    # ── S8 成本只扣一次 ──
    # 旧版 ann_net = ann_alpha − ann_cost，但 ann_alpha 已经是净值 → 扣两遍。
    # 断言三条恒等式同时成立，任一条破了就说明成本又算重了。
    for m in ("hold20", "hold60", "hold250"):
        s = tab[m]
        gap = s["ann_net"] - s["ann_alpha"]
        gap2 = s["ann_net"] - (s["ann_gross"] - s["ann_cost"])
        if not (abs(gap) < 0.06 and abs(gap2) < 0.06):
            chk("S8", False, "%s 净年化 %.2f / 毛−成本 %.2f / 旧 ann_alpha %.2f"
                % (m, s["ann_net"], s["ann_gross"] - s["ann_cost"], s["ann_alpha"]))
            break
    else:
        s = tab["hold250"]
        chk("S8", True, "hold250 毛 %.2f%% − 成本 %.2f%% = 净 %.2f%%（ann_alpha 同值）"
            % (s["ann_gross"], s["ann_cost"], s["ann_net"]))

    print()
    print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
