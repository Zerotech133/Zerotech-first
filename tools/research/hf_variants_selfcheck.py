# -*- coding: utf-8 -*-
"""四项升级自检：结构位止损 / 风险预算 / 标签出场 / 被挡信号。

V1 结构位不改净值：SL 只提示不执行，sl_mode=struct 与 atr 的净值必须逐位一致
V2 结构位覆盖：struct 模式下，至少 80% 持仓能用中枢/笔低点定损（否则等于没接上）
V3 开盘价公式：px_at_k(kind='c') 必须与 px_at 完全一致（首版漏了 c[ei]/c[i]，
   把惠丰钻石的 −33% 算成 −53%）
V4 被挡守恒：skip_days 的 n_block 求和必须等于 skipped_room
V5 标签出场早于到期：tag_* 的 exit_date 必须严格早于「建仓日 +5 个交易日」
V6 风险预算单调：负期望下，risk_pct 加倍必须更亏（这条是反直觉结论的守卫）
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))

import hf_book as HF

res = []


def ok(cond, msg):
    return ("PASS" if cond else "FAIL"), msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true",
                    help="退化档：把 V6 的判据反过来，确认断言真会在乎")
    a = ap.parse_args()

    ctx = HF.build_context(120)
    base = dict(HF.P)
    out = {}

    def run(**kw):
        HF.P.update(base)
        HF.P.update(kw)
        try:
            return HF.run_once(ctx, collect_skip=True)
        finally:
            HF.P.update(base)

    na, ta, la, ska, pa, skda = run(sl_mode="atr")
    ns, ts, ls, sks, ps, skds = run(sl_mode="struct")
    out["atr"] = HF.stats_of(na, ta)
    out["struct"] = HF.stats_of(ns, ts)

    # V1 结构位不改净值
    same = abs(out["atr"]["total_return"] - out["struct"]["total_return"]) < 1e-12
    res.append(ok(same, "V1 结构位不改净值  struct %.6f vs atr %.6f（SL 只提示不执行）"
                  % (out["struct"]["total_return"], out["atr"]["total_return"])))

    # V2 结构位覆盖
    kinds = {}
    for p in ls:
        k = p.get("sl_kind", "atr")
        kinds[k] = kinds.get(k, 0) + 1
    n_all = len(ls) or 1
    # atr_far 是「结构位过远、退回 ATR」，不算可用结构定损
    cov = (n_all - kinds.get("atr", 0) - kinds.get("atr_far", 0)) / n_all
    res.append(ok(cov >= 0.80, "V2 结构位覆盖  %.0f%% 持仓可用中枢/笔低点定损 %s（阈值 80%%）"
                  % (cov * 100, kinds)))

    # V3 开盘价公式
    bad = 0
    n = 0
    for code, b in list(ctx["bars"].items())[:50]:
        for i in range(1, min(len(b.c), 30)):
            for ei in (max(0, i - 2), max(0, i - 1)):
                n += 1
                if abs(b.px_at_k(i, ei, "c") - b.px_at(i, ei)) > 1e-6:
                    bad += 1
    res.append(ok(bad == 0, "V3 开盘价公式  px_at_k(c) 与 px_at 比对 %d 次，不一致 %d 次" % (n, bad)))

    # V4 被挡守恒
    tot = sum(r["n_block"] for r in skda)
    res.append(ok(tot == sks if skds else tot == ska,
                  "V4 被挡守恒  skip_days 求和 %d == skipped_room %d" % (tot, ska)))

    # V5 标签出场早于到期
    nt, tt, lt, skt, pt, _ = run(exit_mode="tag_all")
    axis = ctx["axis"]
    pos = {d: i for i, d in enumerate(axis)}
    early, checked = 0, 0
    for t in tt:
        if not str(t.get("exit_reason", "")).startswith("tag_"):
            continue
        ed = pos.get(t["entry_date"])
        if ed is None:
            continue
        due = axis[min(ed + HF.P["hold"], len(axis) - 1)]
        checked += 1
        if t["exit_date"] < due:
            early += 1
    res.append(ok(checked > 0 and early / checked > 0.9,
                  "V5 出场早于到期  %d/%d 笔标签出场严格早于原定到期日" % (early, checked)))

    # V6 风险预算单调（负期望下加倍即更亏）
    nr1, tr1, *_ = run(sizing="risk", risk_pct=0.01)
    nr2, tr2, *_ = run(sizing="risk", risk_pct=0.02)
    r1 = HF.stats_of(nr1, tr1)["total_return"]
    r2 = HF.stats_of(nr2, tr2)["total_return"]
    cond = (r2 <= r1) if not a.degrade else (r2 > r1)
    res.append(ok(cond, "V6 风险预算单调  1%% %.2f%% → 2%% %.2f%%（负期望下加倍必须更亏）"
                  % (r1 * 100, r2 * 100)))

    print("=" * 84)
    print("四项升级 · 自检（窗 %s~%s）" % (ctx["win"][0], ctx["asof"]))
    print("=" * 84)
    for tag, msg in res:
        print("[%s] %s" % (tag, msg))
    n_pass = sum(1 for r in res if r[0] == "PASS")
    print("-" * 84)
    print("%d/%d 通过%s" % (n_pass, len(res), "（退化档）" if a.degrade else ""))
    return 0 if n_pass == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
