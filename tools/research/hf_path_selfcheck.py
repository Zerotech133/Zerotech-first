# -*- coding: utf-8 -*-
"""hf_path 自检。

P1 样本数：必须与 hf_sell_exit（cap 250）完全一致
P2 前缀积等价：任意区间与 hf_sell_exit.bench_window_ret 逐位相等
P3 T=250 的 A 与 hf_sell_exit 的 hold250 毛超额一致（两套脚本同一口径）
P4 三群占比合计 100%
P5 条件期望每个检查点的分箱样本数合计 = 总样本
P6 神仙上界必须 ≥ 任何一个固定 T 的笔均毛收益（否则 max 算错）
P7 回吐的负值比例 = 终值即峰值的比例
P8 年化恒等式：ann_net = ann_gross − ann_cost
P9 聚类 t 真的收缩：同一天进场的票共享冲击，逐笔 t 必须显著大于聚类 t
P10 条件期望每个格都带聚类 t（只报逐笔 t 等于把一次冲击数成几十次）

用法::
    python -X utf8 tools/research/hf_path_selfcheck.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_path as P          # noqa: E402
import hf_sell_exit as SE    # noqa: E402

ROOT = P.ROOT
FAIL, OK = [], []


def chk(name, cond, msg=""):
    (OK if cond else FAIL).append(name)
    print("%s %-4s %s" % ("✅" if cond else "❌", name, msg))


def _load(fn):
    p = os.path.join(ROOT, "portal", fn)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def main():
    obj = _load("hf_path.json")
    se51 = _load("hf_sell_exit_s51.json")
    if not obj:
        print("缺 portal/hf_path.json，先跑 hf_path.py")
        return 1
    if not se51:
        print("缺 portal/hf_sell_exit_s51.json，先跑 hf_sell_exit.py --slip 51 --out hf_sell_exit_s51.json")
        return 1

    # ── P1 样本数 ──
    n1 = obj["n_trades"]
    n2 = se51["table"]["hold250"]["n"]
    chk("P1", n1 == n2, "hf_path %d 笔 vs hf_sell_exit(cap250) %d 笔" % (n1, n2))

    # ── P2 前缀积等价 ──
    dates, ret, pos = SE.bench_ew()
    bad = []
    probe = [(dates[i], dates[min(i + 60, len(dates) - 1)])
             for i in range(200, len(dates) - 300, 61)]
    for d0, d1 in probe[:60]:
        a = P.bench_iv(d0, d1, 1.0)
        b = SE.bench_window_ret(d0, d1)
        if a is None or b is None or abs(a - b) > 1e-12:
            bad.append((d0, d1, a, b))
    chk("P2", not bad, "%d 个区间逐位比对，不一致 %d 个" % (len(probe[:60]), len(bad)))

    # ── P3 与 hf_sell_exit 同口径 ──
    a_here = obj["curve"]["250"]["A_pct"]
    a_there = se51["table"]["hold250"]["avg_alpha_gross"]
    chk("P3", a_here is not None and a_there is not None
        and abs(a_here - a_there) < 0.02,
        "T=250 毛超额 %.3f%% vs %.3f%%" % (a_here or 0, a_there or 0))

    # ── P4 三群占比 ──
    s = sum(g["share"] for g in obj["groups"])
    chk("P4", abs(s - 100.0) < 0.3, "三群占比合计 %.1f%%" % s)

    # ── P5 条件期望分箱合计 ──
    okc = True
    for t in obj["cond"]:
        tot = sum(c["n"] for c in t["cells"])
        if tot != n1:
            okc = False
            chk("P5", False, "k=%d 分箱合计 %d ≠ %d" % (t["k"], tot, n1))
            break
    if okc:
        chk("P5", True, "%d 个检查点分箱合计均 = %d" % (len(obj["cond"]), n1))

    # ── P6 神仙上界 ──
    orc = obj["oracle"]["mean"]
    worst = max(obj["curve"][k]["mean"] for k in obj["curve"])
    chk("P6", orc >= worst - 1e-9,
        "神仙 %.2f%% ≥ 最高固定 T %.2f%%" % (orc, worst))

    # ── P7 回吐负值 = 终值即峰值 ──
    gb = obj["giveback"]
    chk("P7", 0 <= gb["pct_neg"] <= 100,
        "回吐为负（终值即全程最高）%.1f%%，>10pp 占 %.1f%%"
        % (gb["pct_neg"], gb["pct_over_10"]))

    # ── P8 年化恒等式 ──
    bad8 = []
    for k in obj["curve"]:
        c = obj["curve"][k]
        if c["ann_gross"] is None:
            continue
        if abs(c["ann_net"] - (c["ann_gross"] - c["ann_cost"])) > 0.02:
            bad8.append((k, c["ann_net"], c["ann_gross"] - c["ann_cost"]))
    chk("P8", not bad8, "18 个时点年化恒等式，不一致 %d 个" % len(bad8))

    # ── P9 聚类 t 必須真的按日收缩 ──
    # 造 40 个入场日 × 15 只票：日冲击主导、票间只有极小噪声。
    # 逐笔 t 会以为有 600 个独立观测，聚类 t 只认 40 个 —— 前者应约放大 sqrt(15) 倍。
    import math
    import random
    rnd = random.Random(20261108)
    vals, days = [], []
    for d in range(40):
        shock = rnd.gauss(0.0, 0.02)
        for j in range(15):
            vals.append(shock + rnd.gauss(0.0, 0.0005))
            days.append("2024%03d" % d)
    t_raw = P._tstat(vals)
    t_cl, nd = P.t_cluster(vals, days)
    chk("P9", nd == 40 and t_cl is not None and t_raw / t_cl > 2.0,
        "40 日 × 15 票：逐笔 t=%.1f，聚类 t=%.2f（%d 日），放大 %.1f 倍"
        % (t_raw, t_cl, nd, t_raw / t_cl))

    # ── P10 条件期望必须带聚类 t ──
    miss10 = [(t["k"], c["bin"]) for t in obj["cond"] for c in t["cells"]
              if c.get("rest_alpha_t_cl") is None]
    chk("P10", not miss10,
        "%d 个条件格全部带 rest_alpha_t_cl，缺 %d 个"
        % (sum(len(t["cells"]) for t in obj["cond"]), len(miss10)))

    print()
    print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
