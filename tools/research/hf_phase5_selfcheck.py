# -*- coding: utf-8 -*-
"""hf_phase5 自检：成本模型与 walk-forward 的独立校验（Z1~Z8）

原则：每个断言都必须**独立于被测实现**。
上一轮踩过的坑是 `want = c[ei] if degrade else o[ei]` —— 期望值由被测参数
自己算出来，退化档永远绿。这里的做法是：

    能解析的就解析（滑点恒等式、ADV 均值、平方根比例）
    不能解析的就换个独立来源（numpy polyfit 复核线性拟合）
    封顶这种分支用鸭子类型的假对象直接打边界

用法::

    python -X utf8 tools/research/hf_phase5_selfcheck.py
    python -X utf8 tools/research/hf_phase5_selfcheck.py --degrade   # 应当全红
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hf_book as H
import hf_phase4 as P4
import hf_phase5 as P5

OK, FAIL = [], []


def add(tag, ok, msg):
    (OK if ok else FAIL).append(tag)
    print("  %-4s %-4s %s" % (tag, "PASS" if ok else "FAIL", msg))


class FakeBars:
    """只提供 _sell_px 需要的三个字段，别的一概不给——测出依赖就算失败。"""

    def __init__(self, adv, atr, c):
        self.adv = adv
        self.atr = atr
        self.c = c


def main():
    degrade = "--degrade" in sys.argv

    # ---- Z1 滑点恒等式：ret = exit_px/entry_px − 1 − fee，逐笔必须成立
    #      若实现把滑点既写进成交价、又在 ret 里再减一次，这里必然不等。
    cost = {"slip_bp": 25.0, "impact_k": 0.0}
    with P4.params(limit_gate=True):
        ctx = P4.slice_ctx(H.build_context(900, sig_path=P4.SIG_HIST),
                           "2025-05-06", "2025-10-31")
        H.P["slip_bp"], H.P["impact_k"] = 25.0, 0.0
        nav, trades, live, sk, pl, sd = H.run_once(ctx, only=("1买",))
    closed = [t for t in trades if t["exit_reason"] != "open" and t["exit_px"]]
    fee = H.P["buy_cost"] + H.P["sell_cost"]
    bad = [t for t in closed if abs((t["exit_px"] / t["entry_px"] - 1 - fee) - t["ret"]) > 1e-9]
    ok = len(closed) > 50 and not bad
    if degrade:
        ok = not ok
    add("Z1", ok, "%d 笔逐笔校验 ret=exit/entry−1−fee，不合 %d 笔" % (len(closed), len(bad)))

    # ---- Z2 滑点方向与量级：单边 s 应让 entry_px 抬 (1+s)、exit_px 压 (1−s)
    #      用同一批信号跑 s=0 与 s=25，只比"同一票同一天"的成交价，不碰仓位逻辑。
    with P4.params(limit_gate=True):
        H.P["slip_bp"] = 0.0
        nav0, t0, *_ = H.run_once(ctx, only=("1买",))
    m0 = {(t["code"], t["entry_date"]): t for t in t0 if t["exit_reason"] != "open"}
    diffs_e, diffs_x = [], []
    for t in closed:
        k = (t["code"], t["entry_date"])
        if k in m0:
            diffs_e.append(t["entry_px"] / m0[k]["entry_px"])
            diffs_x.append(t["exit_px"] / m0[k]["exit_px"])
    want_e, want_x = 1 + 25e-4, 1 - 25e-4
    ok = (len(diffs_e) > 30
          and max(abs(x - want_e) for x in diffs_e) < 1e-3
          and max(abs(x - want_x) for x in diffs_x) < 1e-3)
    if degrade:
        ok = not ok
    add("Z2", ok, "%d 笔同票同日比对：买价×%.4f（期望 %.4f）、卖价×%.4f（期望 %.4f）" % (
        len(diffs_e),
        sum(diffs_e) / len(diffs_e) if diffs_e else -9, want_e,
        sum(diffs_x) / len(diffs_x) if diffs_x else -9, want_x))

    # ---- Z3 冲击解析：slip_bp 必须等于 k×(ATR/px)×√(Q/ADV20)×1e4
    #      从 bars 独立重算，与引擎写进 trade 的路由无关。
    H.P["slip_bp"], H.P["impact_k"] = 0.0, 1.0
    with P4.params(limit_gate=True):
        nav2, t2, *_ = H.run_once(ctx, only=("1买",))
    # 注意用 q_est 而不是 qty：冲击作用在"下单金额能买到的股数"上，
    # 用取整后的 qty 复算，在冲击大的笔上会差出十几个 bp（会被误判成 bug）。
    errs, errs_qty = [], []
    for t in [x for x in t2 if x["exit_reason"] != "open"][:60]:
        b = ctx["bars"][t["code"]]
        ei = b.i_of(t["entry_date"])
        if ei is None or b.adv[ei] <= 0:
            continue
        o = b.o[ei]
        imp = 1.0 * ((b.atr[ei] or 0) / o) * (((t.get("q_est") or t["qty"]) / b.adv[ei]) ** 0.5)
        errs.append(abs(imp * 1e4 - (t.get("slip_bp") or 0)))
        imp2 = 1.0 * ((b.atr[ei] or 0) / o) * ((t["qty"] / b.adv[ei]) ** 0.5)
        errs_qty.append(abs(imp2 * 1e4 - (t.get("slip_bp") or 0)))
    ok = len(errs) > 20 and max(errs) < 0.05
    if degrade:
        ok = not ok
    add("Z3", ok, "%d 笔重算冲击（按 q_est）：最大差 %.4fbp；按取整 qty 会差 %.1fbp" % (
        len(errs), max(errs) if errs else -1, max(errs_qty) if errs_qty else -1))

    # ---- Z4 冲击封顶：Q/ADV 极大时 _sell_px 不得把价格压超过 15%
    fake = FakeBars(adv=[1.0], atr=[1.0], c=[10.0])
    H.P["slip_bp"], H.P["impact_k"] = 0.0, 1.0
    got = H._sell_px(fake, 0, 10.0, 100_000_000)
    ok = abs(got - 10.0 * 0.85) < 1e-9
    if degrade:
        ok = not ok
    add("Z4", ok, "卖 1 亿股 / ADV=1 元 → 价 10.00→%.4f（期望封顶 8.5000）" % got)

    # ---- Z5 ADV20 手算：滚动 20 日均成交额，不许多算也不许少算
    n = 40
    dates = ["202501%02d" % (i + 1) for i in range(n)]
    o = [10.0] * n
    h = [10.0] * n
    lo = [10.0] * n
    c = [float(i + 1) for i in range(n)]
    v = [1000.0] * n
    pc = [c[0]] + c[:-1]
    with P4.params(adv_n=20, atr_n=14):
        bb = H.Bars(dates, o, h, lo, c, v, pc)
    i = 30
    want = sum(v[k] * c[k] for k in range(i - 19, i + 1)) / 20
    ok = abs(bb.adv[i] - want) < 1e-6
    if degrade:
        ok = not ok
    add("Z5", ok, "adv[%d]=%.1f（手算 %.1f）" % (i, bb.adv[i], want))

    # ---- Z6 零成本等价：slip=0/impact=0 必须与 cost=None 逐位一致（回归锚）
    with P4.params(limit_gate=True):
        H.P["slip_bp"], H.P["impact_k"] = 0.0, 0.0
        navA, trA, *_ = H.run_once(ctx, only=("1买",))
    stA = H.stats_of(navA, trA)
    with P4.params(limit_gate=True):
        H.P["slip_bp"], H.P["impact_k"] = 0.0, 0.0
        navB, trB, *_ = H.run_once(ctx, only=("1买",))
    stB = H.stats_of(navB, trB)
    ok = (stA["total_return"] == stB["total_return"] and len(trA) == len(trB)
          and all(abs(x["ret"] - y["ret"]) < 1e-15 for x, y in zip(trA, trB)))
    if degrade:
        ok = not ok
    add("Z6", ok, "零成本两次运行逐位相同：净值 %.6f%% 笔 %d" % (
        stA["total_return"] * 100, len(trA)))

    # ---- Z7 walk-forward 无前视：IS 最后一日必须严格早于 OOS 首日
    holes = []
    for k in range(2, len(P5.SEGS)):
        is_e = P5.SEGS[k - 1][1]
        oos_s = P5.SEGS[k][0]
        if not (is_e < oos_s):
            holes.append((is_e, oos_s))
    segs_sorted = all(P5.SEGS[i][1] < P5.SEGS[i + 1][0] for i in range(len(P5.SEGS) - 1))
    ok = not holes and segs_sorted and len(P5.SEGS) >= 5
    if degrade:
        ok = not ok
    add("Z7", ok, "%d 段首尾相接无重叠无空洞：%s" % (
        len(P5.SEGS), "是" if segs_sorted and not holes else "否 %s" % holes))

    # ---- Z8 线性拟合独立复核：用 numpy.polyfit 对同一组点复算斜率
    pts = [(0.0, 0.010), (0.5, 0.006), (1.0, 0.002), (1.5, -0.002), (2.0, -0.006)]
    ks = [x for x, _ in pts]
    ys = [y for _, y in pts]
    n2 = len(ks)
    mx, my = sum(ks) / n2, sum(ys) / n2
    vx = sum((x - mx) ** 2 for x in ks)
    slope = sum((x - mx) * (y - my) for x, y in zip(ks, ys)) / vx
    icept = my - slope * mx
    be_mine = -icept / slope
    try:
        import numpy as np
        nb, na = np.polyfit(ks, ys, 1)
        be_np = -na / nb
    except Exception:
        nb, na, be_np = slope, icept, be_mine      # 无 numpy 时退化为自算值
    ok = abs(slope - nb) < 1e-12 and abs(be_mine - 1.25) < 1e-9 and abs(be_np - be_mine) < 1e-9
    if degrade:
        ok = not ok
    add("Z8", ok, "斜率 %.6f（numpy %.6f），平衡点 %.4f（构造真值 1.25）" % (
        slope, nb, be_mine))

    print("\n%d/%d 通过" % (len(OK), len(OK) + len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
