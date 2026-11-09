# -*- coding: utf-8 -*-
"""hf_phase4 自检：把三项结论的算法逐条钉死。

每一项断言都**独立于被检验的参数**：期望值来自构造或外部实现，
不是"用同一个函数再算一遍"——那种同义反复的自检，退化档永远绿。

Y1 回归锚：161 天台账窗口、闸门全关时必须逐位等于 −16.3925%
Y2 敞口闸门单调：avg_util 随 gross 严格递减（闸门真的在闸）
Y3 极限档：gross=0.05 时仓位 <12%、净值变动 <5%（不崩、不爆）
Y4 窗口几何：5 段互不重叠、都在 axis 内、各段 ≥80 个交易日
Y5 笔均超额：手算 5 笔（每笔收益 − 指数同期收益）与实现比对
Y6 beta/alpha：与 numpy.polyfit 独立比对；纯 beta 合成序列 alpha≈0
Y7 涨跌停幅度：板块 → 幅度映射逐条核对
Y8 涨停闸门：开闸后必须真的挡掉交易（limit_block>0），且不过度挡（<20%）

用法::

    python -X utf8 tools/research/hf_phase4_selfcheck.py
    python -X utf8 tools/research/hf_phase4_selfcheck.py --degrade
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))
import hf_book as H          # noqa: E402
import hf_phase4 as P4       # noqa: E402


def check(degrade=False):
    res = []

    def add(k, ok, msg):
        res.append((k, bool(ok), msg))

    # ---- Y4 窗口几何（不需要跑模拟，先做）
    axis = H.load_axis()
    win8 = [(c, P4._n8(s), P4._n8(e)) for c, s, e, _ in P4.WINDOWS]
    spans = []
    for c, s, e in win8:
        xs = [d for d in axis if s <= d <= e]
        spans.append((c, s, e, len(xs), xs[0] if xs else None, xs[-1] if xs else None))
    ok = all(sp[3] >= 80 for sp in spans)
    for i in range(len(spans) - 1):
        if spans[i][5] is None or spans[i + 1][4] is None:
            ok = False
        elif spans[i][5] >= spans[i + 1][4]:
            ok = False          # 前一段末日 >= 后一段首日 → 重叠
    if degrade:
        ok = not ok
    add("Y4", ok, "5 段：%s" % "  ".join(
        "%s %d日" % (sp[0], sp[3]) for sp in spans))

    # ---- Y7 涨跌停幅度
    cases = [("688111.SH", "金山办公", 0.20), ("300750.SZ", "宁德时代", 0.20),
             ("920454.BJ", "同心传动", 0.30), ("600519.SH", "贵州茅台", 0.10),
             ("000001.SZ", "*ST某某", 0.05), ("830799.BJ", "艾融软件", 0.30)]
    bad = [(c, n, H.limit_pct(c, n), w) for c, n, w in cases if H.limit_pct(c, n) != w]
    if degrade:
        bad = [] if bad else [("---", "---", 0, 0)]
    add("Y7", not bad, "板块幅度 %d/%d 正确%s" % (
        len(cases) - len(bad), len(cases),
        "" if not bad else "  错：%s" % bad[:2]))

    # ---- Y6 beta/alpha：合成纯 beta 序列 + numpy 独立比对
    import random
    random.seed(11)
    xs = [random.gauss(0, 0.012) for _ in range(240)]
    BETA, ALPHA = 1.35, 0.00042
    rs = [ALPHA + BETA * x for x in xs]
    # idxc[d_i] 必须是「i 日收盘」，且 nav[d_i].ret 是「i 日相对 i−1 日」，
    # 所以累乘要在赋值**之前**完成，否则两条序列错位一天，
    # 相关性会塌到 0.09（首版就是这么写错的，测出来的 beta 才 0.093）。
    idxc, lvl, nav = {}, 100.0, []
    for i, x in enumerate(xs):
        lvl *= (1 + x)
        d = "%08d" % (20240101 + i)
        idxc[d] = lvl
        nav.append({"d": d, "ret": rs[i]})
    ba = P4.beta_alpha(nav, idxc)
    try:
        import numpy as np
        A = np.polyfit(xs, rs, 1)
        nb, na = float(A[0]), float(A[1])
    except Exception:
        nb, na = BETA, ALPHA       # 无 numpy 时退化为构造真值
    ok = (ba is not None and abs(ba["beta"] - nb) < 0.02
          and abs(ba["alpha_d"] - na) < 2e-5
          and abs(ba["beta"] - BETA) < 0.02 and abs(ba["alpha_d"] - ALPHA) < 2e-5
          and ba["alpha_t"] > 20)
    if degrade:
        ok = not ok
    add("Y6", ok, "合成 beta=%.2f alpha=%.5f → 测得 beta=%.3f alpha=%.5f t=%s" % (
        BETA, ALPHA, ba["beta"] if ba else -9, ba["alpha_d"] if ba else -9,
        ">1e4" if (ba and (ba["alpha_t"] or 0) > 1e4) else (ba["alpha_t"] if ba else -9)))

    # ---- Y1/Y2/Y3/Y5/Y8 需要真跑
    print("  构建 161 天台账上下文…")
    ctx = H.build_context(120)
    idxc2 = H.load_index_close()

    # Y1 回归锚
    with P4.params(gross_cap=1.0, limit_gate=False):
        r0 = P4.run(ctx, idxc2, label="anchor")
    ok = (abs((r0["total_return"] or 0) + 0.163925) < 1e-6
          and abs((r0["max_drawdown"] or 0) + 0.230428) < 1e-6
          and abs((r0["avg_util"] or 0) - 0.8453) < 1e-3
          and r0["n_trades"] == 900)
    if degrade:
        ok = not ok
    add("Y1", ok, "净值 %.4f%%  回撤 %.4f%%  仓位 %.2f%%  笔 %d" % (
        (r0["total_return"] or 0) * 100, (r0["max_drawdown"] or 0) * 100,
        (r0["avg_util"] or 0) * 100, r0["n_trades"]))

    # Y2 敞口闸门单调
    utils = []
    for g in (1.0, 0.8, 0.6, 0.4):
        with P4.params(gross_cap=g):
            rr = P4.run(ctx, idxc2, label="g%.1f" % g)
        utils.append(rr["avg_util"] or 0)
    ok = all(utils[i] > utils[i + 1] for i in range(len(utils) - 1))
    if degrade:
        ok = not ok
    add("Y2", ok, "仓位序列 %s（须严格递减）" % " → ".join("%.1f%%" % (u * 100) for u in utils))

    # Y3 极限档
    with P4.params(gross_cap=0.05):
        r_lo = P4.run(ctx, idxc2, label="g0.05")
    ok = ((r_lo["avg_util"] or 0) < 0.12
          and abs(r_lo["total_return"] or 0) < 0.05
          and r_lo["n_trades"] > 0)
    if degrade:
        ok = not ok
    add("Y3", ok, "gross=0.05 → 仓位 %.2f%%  净值 %.3f%%  笔 %d" % (
        (r_lo["avg_util"] or 0) * 100, (r_lo["total_return"] or 0) * 100,
        r_lo["n_trades"]))

    # Y5 笔均超额：手算**全样本**均值，与 per_trade_alpha 的 ALL 比对。
    # 只抽查前 5 笔"看差值大小"是假自检——那根本没跟实现比。
    with P4.params(gross_cap=1.0, limit_gate=False):
        H.P.pop("_n_limit_block", None)
        nav, trades, live, sk, plans, sd = H.run_once(ctx)
    got = P4.per_trade_alpha(trades, idxc2)
    manual = [t["ret"] - (idxc2[t["exit_date"]] / idxc2[t["entry_date"]] - 1)
              for t in trades
              if t["exit_reason"] != "open" and t["entry_date"] in idxc2
              and t["exit_date"] in idxc2]
    m_mean = sum(manual) / len(manual) if manual else None
    impl = got.get("ALL", {})
    diff = abs(m_mean - impl["excess"]) if (m_mean is not None and
                                            impl.get("excess") is not None) else 9
    # per_trade_alpha 输出 round 到 6 位，差 5e-7 属于舍入，不是算法错
    ok = (impl.get("n", 0) == len(manual) and len(manual) > 800 and diff < 1e-6)
    if degrade:
        ok = not ok
    add("Y5", ok, "手算 n=%d 均值 %+.6f vs 实现 n=%d %+.6f，差 %.2e" % (
        len(manual), m_mean or 0, impl.get("n", 0), impl.get("excess") or 0, diff))

    # Y8 涨停闸门
    with P4.params(gross_cap=1.0):
        rg = P4.run(ctx, idxc2, label="gate", gate=True)
    n_gate, n_tot = rg["limit_block"], rg["n_trades"] + rg["limit_block"]
    ok = (n_gate > 0) and (n_gate / max(1, n_tot) < 0.20)
    if degrade:
        ok = not ok
    add("Y8", ok, "挡掉 %d 笔（占候选 %.1f%%，须 0<占比<20%%）" % (
        n_gate, n_gate / max(1, n_tot) * 100))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()
    res = check(a.degrade)
    print("hf_phase4 自检（%d 条）%s" % (len(res), "· 退化档" if a.degrade else ""))
    bad = 0
    for k, ok, msg in res:
        print("  %-3s %s  %s" % (k, "PASS" if ok else "FAIL", msg))
        bad += 0 if ok else 1
    print("→ %d/%d 通过" % (len(res) - bad, len(res)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
