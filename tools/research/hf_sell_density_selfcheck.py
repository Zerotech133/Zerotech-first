# -*- coding: utf-8 -*-
"""hf_sell_density 自检。

Z1 日历单调唯一，且与信号日期同为 8 位
Z2 所有间隔 >= 1（严格晚于确认日）
Z3 合成用例：间隔必须精确等于日历位置差
Z4 退化档（删光卖点）→ 100% 右删失、中位 None
Z5 within_N 随 N 单调不减（分布函数的定义）
Z6 有效样本数 = 数据里 1买 的确认日去重数（不是行数）
Z7 右删失 + 有卖点 = 总数（不能有样本静默蒸发）

用法::
    python -X utf8 tools/research/hf_sell_density_selfcheck.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hf_sell_density as D

FAIL = []
OK = []


def chk(name, cond, msg=""):
    (OK if cond else FAIL).append(name)
    print("%s %-4s %s" % ("✅" if cond else "❌", name, msg))


def main():
    cal, cal_idx = D.load_calendar()

    # ── Z1 ──
    ok = bool(cal) and all(len(d) == 8 and d.isdigit() for d in cal) and cal == sorted(set(cal))
    chk("Z1", ok, "日历 %d 日，%s ~ %s，全 8 位且严格递增" % (
        len(cal), cal[0] if cal else "-", cal[-1] if cal else "-"))

    # ── Z3 合成用例（用真实日历里的位置，不假设节假日）──
    if len(cal) > 40:
        i0 = 100
        d0, d1 = cal[i0], cal[i0 + 7]
        g = D._gap(cal_idx, cal, d0, d1)
        chk("Z3", g == 7, "合成 %s → %s 期望 7 实得 %s" % (d0, d1, g))
        g0 = D._gap(cal_idx, cal, d0, "19900101")
        chk("Z3b", g0 is None, "不在日历内的日期必须返回 None，实得 %s" % g0)
    else:
        chk("Z3", False, "日历太短，无法构造用例")

    # ── 跑真数据 ──
    res = D.run(degrade=False, write=False)
    da = res["dist_any_sell"]
    d1s = res["dist_1sell"]

    # ── Z7 样本守恒 ──
    chk("Z7", da["n_with"] + da["n_censored"] == da["n_total"],
        "%d + %d == %d" % (da["n_with"], da["n_censored"], da["n_total"]))

    # ── Z6 有效样本数 == 去重后的 1买 确认日数 ──
    per, meta = D.load_signals(degrade=False)
    want = sum(len(g["buy"]) for g in per.values())
    chk("Z6", want == da["n_total"], "去重 1买 确认日 %d，统计总数 %d" % (want, da["n_total"]))

    # ── Z5 within_N 单调 ──
    seq = [da["within_%d" % h] for h in D.HOLD_REF]
    mono = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
    chk("Z5", mono, " → ".join("%.2f%%" % x for x in seq))

    # ── Z2 间隔最小 1 ──
    # 用 by_year 中位数反推不可靠，直接重扫一遍最小间隔
    min_gap = None
    for code, g in per.items():
        if not g["buy"]:
            continue
        sd = [d for d, _ in g["sell"]]
        for d0 in g["buy"]:
            import bisect
            j = bisect.bisect_right(sd, d0)
            if j < len(sd):
                gp = D._gap(cal_idx, cal, d0, sd[j])
                if gp is not None:
                    min_gap = gp if min_gap is None else min(min_gap, gp)
    chk("Z2", min_gap is not None and min_gap >= 1, "最小间隔 %s 交易日" % min_gap)

    # ── Z4 退化档 ──
    deg = D.run(degrade=True, write=False)
    dd = deg["dist_any_sell"]
    chk("Z4", dd["n_with"] == 0 and dd["n_censored"] == dd["n_total"] and dd["median"] is None,
        "删光卖点后 有卖点=%d 删失=%d 中位=%s" % (dd["n_with"], dd["n_censored"], dd["median"]))

    # ── 主结论打印 ──
    print()
    print("主结论：1买 之后第一个卖点 中位 %s 交易日，≤20 日内只 %.2f%%，右删失 %.1f%%" % (
        da["median"], da["within_20"] or 0, 100.0 * da["n_censored"] / da["n_total"]))
    print("        仅 1卖 口径 中位 %s，≤20 日内 %.2f%%" % (d1s["median"], d1s["within_20"] or 0))

    print()
    print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
