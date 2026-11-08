# -*- coding: utf-8 -*-
"""候风盘四项升级的对照产出：结构位止损 / 风险预算 / 标签出场 / 被挡信号按日拆。

产出 portal/hf_variants.json，供前端「买点对照」之外的「升级验证」区展示。

四件事各自要回答的问题：
1. 结构位止损：40 只持仓里，有多少能用日线中枢 ZG / 笔低点定止损？ATR 还剩多少兜底？
2. 风险预算：把仓位公式从逆波动率等权换成「净值×1% ÷ max(2ATR/px,4%)」会怎样？
3. 标签出场：T/M/P 三标签真的执行，比死拿 5 日好吗？好多少？是「卖对了」还是「卖早了」？
4. 被挡信号：1619 个被持仓上限挡掉的信号，分布在哪些日子？哪些日子在空转？

反事实口径（第 3 项的关键）：每笔标签出场都记下"如果按原计划持到第 5 日"的价格，
两者相减即为该次出场的净贡献。正数=卖早了，负数=卖对了。
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))

import hf_book as HF

OUT = os.path.join(ROOT, "portal", "hf_variants.json")

VARIANTS = [
    ("base", "基线：ATR 止损 · 逆波动率等权 · 死拿 5 日", {"sl_mode": "atr"}),
    ("struct_sl", "结构位止损：中枢 ZG / 笔低点优先", {"sl_mode": "struct"}),
    ("risk", "风险预算：净值×1% ÷ max(2ATR/px, 4%)", {"sizing": "risk"}),
    ("risk_2pct", "风险预算 2%（加倍下注）", {"sizing": "risk", "risk_pct": 0.02}),
    ("tag_all", "标签出场：红/橙次日开盘全卖", {"exit_mode": "tag_all"}),
    ("tag_red", "标签出场：只有红卖", {"exit_mode": "tag_red"}),
    ("tag_half", "标签出场：橙卖一半 + 红全清", {"exit_mode": "tag_half"}),
]


def run_variant(ctx, kw):
    base = dict(HF.P)
    HF.P.update(base)
    HF.P.update(kw)
    try:
        nav, trades, live, skipped, plans, skip_days = HF.run_once(ctx, collect_skip=True)
    finally:
        HF.P.update(base)
    st = HF.stats_of(nav, trades)
    util = st.get("avg_util") or 0
    st["ret_per_util"] = round(st["total_return"] / util, 6) if util > 1e-9 else None
    st["skipped_room"] = skipped
    ct = [t for t in trades if t.get("exit_date")]
    st["exit_mix"] = dict(Counter(t.get("exit_reason", "") for t in ct))
    st["avg_ret_w"] = round(
        sum(t["amt"] * t["ret"] for t in ct) / (sum(t["amt"] for t in ct) or 1.0), 6)
    # 反事实：标签出场相对"持到期"的净贡献
    adv = [(t["alt_exit_px"] / t["entry_px"] - 1) - (t["exit_px"] / t["entry_px"] - 1)
           for t in ct if t.get("alt_exit_px")]
    cf = None
    if adv:
        by = {}
        for k in ("tag_red", "tag_orange", "tag_half"):
            xs = [(t["alt_exit_px"] / t["entry_px"] - 1) - (t["exit_px"] / t["entry_px"] - 1)
                  for t in ct if t.get("exit_reason") == k and t.get("alt_exit_px")]
            if xs:
                by[k] = {"n": len(xs), "adv": round(statistics.mean(xs) * 100, 3),
                         "early": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1)}
        cf = {"n": len(adv), "adv": round(statistics.mean(adv) * 100, 3),
              "early": round(sum(1 for x in adv if x > 0) / len(adv) * 100, 1),
              "by_kind": by}

    pos_sl = Counter(p.get("sl_kind", "atr") for p in live)
    sl_basis = [{"code": p["code"], "name": p.get("name", ""), "kind": p.get("sl_kind"),
                 "sl": round(p.get("sl") or 0, 3), "atr": round(p.get("sl_atr") or 0, 3),
                 "px": round(p.get("px") or 0, 3), "basis": p.get("sl_basis", "")}
                for p in live[:8]]
    return {"stats": st, "counterfactual": cf,
            "sl_mix": dict(pos_sl), "sl_examples": sl_basis,
            "skip_days": skip_days}


def skip_summary(days, n_days):
    if not days:
        return None
    by_type = Counter()
    for r in days:
        for k, v in (r.get("by_type") or {}).items():
            by_type[k] += v
    tot = sum(r["n_block"] for r in days)
    # 分档直方图：当日被挡数落在哪个区间
    bins = [(1, 5), (6, 10), (11, 20), (21, 50), (51, 10 ** 9)]
    hist = []
    for lo, hi in bins:
        n = sum(1 for r in days if lo <= r["n_block"] <= hi)
        if n:
            hist.append({"label": ("%d-%d" % (lo, hi)) if hi < 10 ** 9 else ("%d+" % lo),
                         "days": n,
                         "blocks": sum(r["n_block"] for r in days if lo <= r["n_block"] <= hi)})
    top = sorted(days, key=lambda r: -r["n_block"])[:10]
    return {
        "n_days_with_block": len(days), "n_days_total": n_days,
        "n_block": tot, "mean_per_block_day": round(tot / len(days), 1),
        "zero_room_days": sum(1 for r in days if r["room"] == 0),
        "by_type": dict(by_type), "hist": hist,
        "top_days": [{k: r[k] for k in ("d", "n_cand", "n_take", "n_block", "n_live")} for r in top],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    ctx = HF.build_context(a.days)
    print("窗 %s~%s  %d 只票" % (ctx["win"][0], ctx["asof"], len(ctx["bars"])))

    idxc = HF.load_index_close()
    d0 = ctx["win"][0]
    i0 = idxc.get(d0)
    i1 = idxc.get(ctx["asof"])
    idx_ret = round(i1 / i0 - 1, 6) if i0 and i1 else None

    out_v = {}
    skip_ref = None
    for tag, label, kw in VARIANTS:
        r = run_variant(ctx, kw)
        st = r["stats"]
        if idx_ret is not None:
            st["alpha"] = round(st["total_return"] - idx_ret, 6)
        out_v[tag] = {"label": label, "params": kw, "stats": st,
                      "counterfactual": r["counterfactual"],
                      "sl_mix": r["sl_mix"], "sl_examples": r["sl_examples"]}
        if tag == "base":
            skip_ref = skip_summary(r["skip_days"], st.get("n_days") or 0)
        print("%-10s %-34s 净值%+7.2f%% 笔均%+6.2f%% 仓位%5.1f%% 敞口归%+7.2f%% 笔%4d 持%.1f日 SL%s" % (
            tag, label[:32], st["total_return"] * 100, st["avg_ret"] * 100,
            st["avg_util"] * 100, (st["ret_per_util"] or 0) * 100, st["n_trades"],
            st["avg_hold"], r["sl_mix"]))

    out = {
        "asof": ctx["asof"],
        "meta": {"engine": "hf_variants v1 · 四项升级同引擎对照",
                 "window": [d0, ctx["asof"]], "params": HF.P,
                 "index_ret": idx_ret,
                 "note": "结构位止损只影响提示位，不改净值（SL 不执行）；"
                         "其余变体的净值差异须并看 ret_per_util 与 avg_util。"},
        "variants": out_v,
        "skip": skip_ref,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print("\n=== 结构位覆盖（基线持仓 40 只）===")
    print(dict(out_v["struct_sl"]["sl_mix"]))
    cf = out_v["tag_all"]["counterfactual"]
    if cf:
        print("\n=== 标签出场反事实（%d 笔）===" % cf["n"])
        print("相对持到期 %+.2f%%（负=卖对了），卖早比例 %.1f%%" % (cf["adv"], cf["early"]))
        for k, v in cf["by_kind"].items():
            print("  %-11s n=%3d 相对持到期 %+.2f%% 卖早 %.1f%%" % (k, v["n"], v["adv"], v["early"]))
    if skip_ref:
        print("\n=== 被挡信号 ===")
        print("被挡 %d 个，分布 %d 天；满仓无空位 %d 天；单日均 %.1f 个" % (
            skip_ref["n_block"], skip_ref["n_days_with_block"],
            skip_ref["zero_room_days"], skip_ref["mean_per_block_day"]))
        print("按类型:", skip_ref["by_type"])
    print("→ %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
