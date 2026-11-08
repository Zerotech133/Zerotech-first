# -*- coding: utf-8 -*-
"""hf_regauge：G3 新旧口径对照表（2026-11-08 第十七轮）。

## 这份表要回答什么

前十六轮的 α 全部建在 `000985.CSI 市值加权 + 隐含 β=1` 上。第十六轮用
另一条独立样本证明它高估 3.5~25 倍，但那只覆盖了「持到卖点」一个场景。
**十四轮里所有依赖超额数字的结论，都需要在新口径下重新判一次。**

本脚本不重跑引擎——它只做对照：读入

    hf_phase5.json      新口径（等权全A + 逐笔β）
    hf_phase5_csi.json  旧口径（000985 + β=1）
    hf_phase6.json / hf_phase6_csi.json   同理

逐条并排，并**自动判定哪些结论翻转了**。判定规则写死在这里，不是看完
结果再定：

    |t_old| >= 2 且 |t_new| < 2  →  失效（原来的显著性是新口径下站不住的）
    |t_old| < 2  且 |t_new| >= 2  →  新增（新口径下才显现）
    其余                          →  保持

这条规则只看显著性是否跨过 2.0 这条线，不看数值大小——数值大小会被
基准涨幅直接放大缩小，只有 t 值衡量「这个差异能不能和噪声区分开」。

## 为什么必须并排

单看新数字会得到一个错误印象：「α 变小了，所以更差了」。但 α 变小不等于
策略变差——原来那部分 α 本来就不是策略赚的，是小盘因子。差的是「记载」，
不是「能力」。并排才能看清哪些是真的、哪些是记账错误。

用法::

    python -X utf8 tools/research/hf_regauge.py
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import hf_bench as HB          # noqa: E402

PORTAL = os.path.join(ROOT, "portal")
OUT = os.path.join(PORTAL, "hf_regauge.json")

T_LINE = 2.0                   # 显著性线，判定规则的一半


def _load(name):
    p = os.path.join(PORTAL, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _f(x, nd=4):
    return round(x, nd) if isinstance(x, (int, float)) else None


def verdict_of(t_old, t_new):
    """显著性翻转判定。规则见模块 docstring，写死不看结果。"""
    a = abs(t_old) if isinstance(t_old, (int, float)) else None
    b = abs(t_new) if isinstance(t_new, (int, float)) else None
    if a is None or b is None:
        return "缺数据"
    if a >= T_LINE and b < T_LINE:
        return "失效"
    if a < T_LINE and b >= T_LINE:
        return "新增"
    return "保持"


# ---------------------------------------------------------------- phase5
def cmp_p5_a(old, new):
    """A 段：成本档 × 变体。看三个数：净值（应不变）、每单位α、t。"""
    if not old or not new:
        return []
    orows = {(r["cost"], r["variant"]): r for r in old["a"]["rows"]}
    out = []
    for r in new["a"]["rows"]:
        k = (r["cost"], r["variant"])
        o = orows.get(k)
        if not o:
            continue
        out.append({
            "cost": r["cost"], "variant": r["variant"],
            "nav": _f(r["nav"], 6),
            "nav_old": _f(o["nav"], 6),
            # 净值必须逐位相同：换基准不该动账本，动了就是改错了地方
            "nav_same": (abs((r["nav"] or 0) - (o["nav"] or 0)) < 1e-9),
            "exc_old": _f(o["excess"], 6), "exc_new": _f(r["excess"], 6),
            "apu_old_bp": _f((o.get("alpha_per_util") or 0) * 1e4, 2),
            "apu_new_bp": _f((r.get("alpha_per_util") or 0) * 1e4, 2),
            "t_old": o.get("alpha_t"), "t_new": r.get("alpha_t"),
            "verdict": verdict_of(o.get("alpha_t"), r.get("alpha_t")),
        })
    return out


def cmp_p5_d(old, new):
    """D 段：降换手。同样只看每单位α 与 t。"""
    if not old or not new:
        return []
    ok = {(r["hold"], r["cost"]): r for r in old["d"]["rows"]}
    out = []
    for r in new["d"]["rows"]:
        o = ok.get((r["hold"], r["cost"]))
        if not o:
            continue
        out.append({
            "hold": r["hold"], "cost": r["cost"],
            "nav": _f(r["nav"], 6), "nav_old": _f(o["nav"], 6),
            "turnover": r.get("turnover"),
            "avg_ret": _f(r.get("avg_ret"), 6),
            "apu_old_bp": _f((o.get("alpha_per_util") or 0) * 1e4, 2),
            "apu_new_bp": _f((r.get("alpha_per_util") or 0) * 1e4, 2),
            "t_old": o.get("alpha_t"), "t_new": r.get("alpha_t"),
            "verdict": verdict_of(o.get("alpha_t"), r.get("alpha_t")),
        })
    return out


def cmp_p5_b(old, new):
    """B 段：盈亏平衡。笔均口径不受基准影响（只扣成本），每单位α 口径会变。"""
    if not old or not new:
        return {}
    oa, na = old["b"].get("fit_avg_ret") or {}, new["b"].get("fit_avg_ret") or {}
    ou, nu = old["b"].get("fit_alpha_per_util") or {}, new["b"].get("fit_alpha_per_util") or {}
    # 归零点是 fit["be"]（解出的 k），不是 fit["k0"]（那是截距，约 0.001，
    # 首版取错字段，看板上把 0.79 显示成了 0.00）。
    return {
        "be_k_avg_old": _f(oa.get("be"), 3), "be_k_avg_new": _f(na.get("be"), 3),
        "be_k_apu_old": _f(ou.get("be"), 3), "be_k_apu_new": _f(nu.get("be"), 3),
        "r2_apu_old": _f(ou.get("r2"), 4), "r2_apu_new": _f(nu.get("r2"), 4),
        "be_rt_old_bp": old["b"].get("own_be_roundtrip_bp"),
        "be_rt_new_bp": new["b"].get("own_be_roundtrip_bp"),
        "ext_bp": new["b"].get("calib_external_be_roundtrip_bp"),
    }


def cmp_p5_c(old, new):
    """C 段：walk-forward。看「选择器 vs 固定只1买」这个判断有没有变。"""
    if not old or not new:
        return {}
    def _agg(d):
        a = d["c"].get("agg") or {}
        return {k: {"avg_excess": _f((v or {}).get("avg_excess"), 6),
                    "n_win": (v or {}).get("n_win_pos")}
                for k, v in a.items() if isinstance(v, dict)}
    return {"agg_old": _agg(old), "agg_new": _agg(new),
            "note": new["c"].get("note")}


# ---------------------------------------------------------------- phase6
def cmp_p6_a(old, new):
    """A 段：势档分层。本轮最要紧的一段——强市 α 的显著性在这里丢掉的。"""
    if not old or not new:
        return {}
    orows = {r["regime"]: r for r in old["a"]["rows"]}
    rows = []
    for r in new["a"]["rows"]:
        o = orows.get(r["regime"])
        if not o:
            continue
        rows.append({
            "regime": r["regime"], "cn": r["cn"], "n": r["n"],
            "exc_old": _f(o["excess"], 6), "exc_new": _f(r["excess"], 6),
            "t_old": o.get("t"), "t_new": r.get("t"),
            "verdict": verdict_of(o.get("t"), r.get("t")),
        })
    ob = old["a"].get("by_type") or {}
    nb = new["a"].get("by_type") or {}
    by = []
    for tp in ("1买", "2买", "3买"):
        for g in ("强", "中", "弱"):
            ov = ((ob.get(tp) or {}).get(g) or {})
            nv = ((nb.get(tp) or {}).get(g) or {})
            if not nv:
                continue
            by.append({"type": tp, "regime": g, "n": nv.get("n"),
                       "exc_old": _f(ov.get("excess"), 6),
                       "exc_new": _f(nv.get("excess"), 6),
                       "t_old": ov.get("t"), "t_new": nv.get("t"),
                       "verdict": verdict_of(ov.get("t"), nv.get("t"))})
    return {"rows": rows, "by_type": by, "base": new["a"].get("base")}


def cmp_p6_b(old, new):
    """B 段：闸门对比。主判据是每单位敞口收益，跨敞口可比。"""
    if not old or not new:
        return []
    ok = {r["label"]: r for r in old["b"]["rows"]}
    out = []
    for r in new["b"]["rows"]:
        o = ok.get(r["label"])
        if not o:
            continue
        out.append({
            "label": r["label"], "only": r.get("only"),
            "nav": _f(r.get("total_return"), 6), "nav_old": _f(o.get("total_return"), 6),
            "util": r.get("avg_util"), "beta": r.get("beta_eff"),
            "rpu_old": _f(o.get("ret_per_util"), 4), "rpu_new": _f(r.get("ret_per_util"), 4),
            "apu_old_bp": _f((o.get("alpha_per_util") or 0) * 1e4, 2),
            "apu_new_bp": _f((r.get("alpha_per_util") or 0) * 1e4, 2),
            "t_old": o.get("alpha_t"), "t_new": r.get("alpha_t"),
            "verdict": verdict_of(o.get("alpha_t"), r.get("alpha_t")),
        })
    return out


def _cmp_matrix(old, new, sec):
    """C/D 段共用的 6×3 矩阵对照。"""
    if not old or not new:
        return []
    ok = {r["hold"]: r.get("cells") or {} for r in old[sec].get("rows") or []}
    out = []
    for r in new[sec].get("rows") or []:
        h = r["hold"]
        cells = []
        for g in ("强", "中", "弱"):
            o = (ok.get(h) or {}).get(g) or {}
            nv = (r.get("cells") or {}).get(g) or {}
            if not nv:
                continue
            cells.append({"regime": g, "n": nv.get("n"),
                          "exc_old": _f(o.get("excess"), 6),
                          "exc_new": _f(nv.get("excess"), 6),
                          "t_old": o.get("t"), "t_new": nv.get("t"),
                          "verdict": verdict_of(o.get("t"), nv.get("t"))})
        out.append({"hold": h, "nav": r.get("nav"), "util": r.get("util"),
                    "cells": cells})
    return out


def main():
    p5o, p5n = _load("hf_phase5_csi.json"), _load("hf_phase5.json")
    p6o, p6n = _load("hf_phase6_csi.json"), _load("hf_phase6.json")
    if not p5n or not p6n:
        print("缺新口径产物：先跑 hf_phase5.py 与 hf_phase6.py（默认 --bench ew）")
        return 2

    a5 = cmp_p5_a(p5o, p5n)
    d5 = cmp_p5_d(p5o, p5n)
    a6 = cmp_p6_a(p6o, p6n)
    b6 = cmp_p6_b(p6o, p6n)
    c6 = _cmp_matrix(p6o, p6n, "c")
    d6 = _cmp_matrix(p6o, p6n, "d")

    # 全表统计：多少条失效 / 新增 / 保持
    allv = ([x["verdict"] for x in a5] + [x["verdict"] for x in d5]
            + [x["verdict"] for x in a6.get("rows") or []]
            + [x["verdict"] for x in a6.get("by_type") or []]
            + [x["verdict"] for x in b6]
            + [c["verdict"] for m in c6 for c in m["cells"]]
            + [c["verdict"] for m in d6 for c in m["cells"]])
    stat = {k: allv.count(k) for k in ("失效", "新增", "保持", "缺数据")}

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "caliber": {
            "old": "000985.CSI 市值加权 + 隐含 β=1（前十六轮口径，已作废）",
            "new": "等权全A × 逐笔 β（chanlun_alpha.beta_of，入场前 60 日回归，无未来函数）",
            "bench_src": HB.BENCH_SRC,
            "bench_span": HB.bench_span(),
            "rule": "|t|>=2 → <2 判失效；<2 → >=2 判新增；其余保持。规则写死在 hf_regauge.py，不看结果再定。",
            "why_t": "只比 t 不比数值：数值大小被基准涨幅直接放大缩小，只有 t 衡量能否与噪声区分。",
        },
        "summary": stat,
        "p5": {"a": a5, "b": cmp_p5_b(p5o, p5n), "c": cmp_p5_c(p5o, p5n), "d": d5},
        "p6": {"a": a6, "b": b6, "c": c6, "d": d6},
    }
    os.makedirs(PORTAL, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)

    print("判定统计：%s" % stat)
    print()
    print("== phase5 A 段（每单位α bp / t）==")
    for r in a5:
        print("  %-5s %-5s 净 %8.2f%%→%8.2f%%  α/单位 %6.1f→%6.1f bp  t %5.2f→%5.2f  %s"
              % (r["cost"], r["variant"], (r["nav_old"] or 0) * 100,
                 (r["nav"] or 0) * 100, r["apu_old_bp"] or 0, r["apu_new_bp"] or 0,
                 r["t_old"] or 0, r["t_new"] or 0, r["verdict"]))
    print()
    print("== phase6 A 段（势档分层，笔均超额）==")
    for r in a6.get("rows") or []:
        print("  %-6s n=%-5d %+7.2f%% → %+7.2f%%   t %5.2f→%5.2f  %s"
              % (r["cn"], r["n"], (r["exc_old"] or 0) * 100,
                 (r["exc_new"] or 0) * 100, r["t_old"] or 0, r["t_new"] or 0, r["verdict"]))
    print()
    print("== phase6 D 段（只1买 × 势 × 期限）==")
    for m in d6:
        cs = "  ".join("%s %+6.2f%%(t%5.2f→t%5.2f)%s" % (
            c["regime"], (c["exc_new"] or 0) * 100, c["t_old"] or 0,
            c["t_new"] or 0, "" if c["verdict"] == "保持" else "[" + c["verdict"] + "]")
            for c in m["cells"])
        print("  hold%-3s %s" % (m["hold"], cs))
    print()
    print("→ %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
