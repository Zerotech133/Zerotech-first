# -*- coding: utf-8 -*-
"""持有期的稳健性验证 + 账户层落地（K-RES-20261108-TERM-VERIFY）。

`hold_term_lab.py` 得到一个很强的结果：只把持有期从 20 日改成 3 日，日批均
从 −1.20% 翻到 +1.45%，且剔掉最好的 5 天后仍为 +0.13%（本轮唯一一个活过
第五道稳健性的量）。

这个结果好得需要被怀疑。本脚本把可疑之处逐条证伪：

  A 聚合口径   按笔等权 与 按日批等权 是否一致（防「少数大日子撑起均值」）
  B 逐月       是否只有某一个月在做贡献
  C 分类型     1买 / 2买 是否同向（防「只是一类买点」）
  D 成本        短持有最大的风险是成本占比：3 天赚 1.45%，成本若翻 4 倍还剩多少
  E 账户层      用同一套组合引擎跑 hold=3，看 cagr / mdd / util 是否同样改善
                （util 必须一起报：不同 util 的 cagr 不可横向比）

成本口径为什么不写在 load_price_panel 里
----------------------------------------
`net_at` 每次现算净收益，成本作为参数传入，这样可以扫多档成本而不污染面板。
现行 20bp（买 7.5 + 卖 12.5）是产线假设值；真实小单约 25~30bp（含冲击），
所以 40/60/80bp 三档不是浪费——持有期 3 天时，20bp 成本已经吃掉 14% 的毛收益。

用法:
    python tools/research/hold_term_verify.py
    python tools/research/hold_term_verify.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import Pick, build_ratio, load_price_panel  # noqa: E402

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
OUT = os.path.join(ROOT, "portal", "hold_term_verify.json")

HS = (2, 3, 5, 8, 20)
COST_SCEN = ((0.00075, 0.00125, "20bp(现行)"),
             (0.0012, 0.0018, "30bp"),
             (0.0020, 0.0030, "50bp"),
             (0.0032, 0.0048, "80bp"))


def net_of(c0, c1, buy, sell):
    return (c1 * (1.0 - sell)) / (c0 * (1.0 + buy)) - 1.0


def agg(rows, key, by_day=True):
    """聚合。by_day=True 先按日等权再算 t；否则按笔等权。"""
    if by_day:
        d = defaultdict(list)
        for r in rows:
            v = r.get(key)
            if v is not None:
                d[r["entry_date"]].append(v)
        y = np.array([float(np.mean(v)) for _, v in sorted(d.items())], float)
    else:
        y = np.array([r[key] for r in rows if r.get(key) is not None], float)
    if len(y) < 3:
        return None
    m = float(y.mean())
    sd = float(y.std(ddof=1))
    t = float(m / (sd / math.sqrt(len(y)))) if sd > 0 else None
    o = np.sort(y)[::-1]
    return {"n": int(len(y)), "mean_pct": round(m * 100, 3),
            "med_pct": round(float(np.median(y)) * 100, 3),
            "win_pct": round(float((y > 0).mean()) * 100, 1),
            "t": round(t, 2) if t is not None else None,
            "drop5": round(float(o[5:].mean()) * 100, 3) if len(o) > 10 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass") and r.get("mature")]
    codes = sorted({r["code"] for r in passed})
    P = load_price_panel(codes, TDX)
    say("信号 %d 笔｜%d 只票" % (len(passed), len(codes)))

    # 预计算：每笔的 h 日净收益（基准成本档），幂等
    rows = []
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None:
            continue
        rec = {"entry_date": r["entry_date"], "type": r["type"], "code": r["code"],
               "sigdens": r["sigdens"], "ei": i, "xi_h": {}}
        for h in HS:
            j = min(i + h, len(b["d"]) - 1)
            if j <= i:
                continue
            rec["h%d" % h] = net_of(b["c"][i], b["c"][j], 0.00075, 0.00125)
            rec["xi_h"][h] = j
        rows.append(rec)
    say("可算 %d 笔｜%d 天" % (len(rows), len({r["entry_date"] for r in rows})))

    res = {"n": len(rows), "A_agg": {}, "B_month": {}, "C_type": {},
           "D_cost": {}, "E_acct": {}}

    # ── A 两种聚合口径 ──
    say("\n【A】两种聚合口径是否一致（若只在水位口径下成立，就是聚合造假）")
    say("  %3s %18s %18s" % ("h", "按日批等权", "按笔等权"))
    for h in HS:
        s = agg(rows, "h%d" % h, True)
        p = agg(rows, "h%d" % h, False)
        res["A_agg"]["h%d" % h] = {"day": s, "pick": p}
        if s and p:
            say("  %3d  %+6.2f%% t=%+5.2f  %+6.2f%% t=%+5.2f"
                % (h, s["mean_pct"], s["t"] or 0, p["mean_pct"], p["t"] or 0))

    # ── B 逐月 ──
    bym = defaultdict(list)
    for r in rows:
        bym[r["entry_date"][:6]].append(r)
    say("\n【B】逐月：h=3 与 h=20 的日批均")
    say("  %-8s %5s %9s %9s %8s" % ("月份", "天", "h=3", "h=20", "差"))
    for mth in sorted(bym):
        s3 = agg(bym[mth], "h3", True)
        s20 = agg(bym[mth], "h20", True)
        if not (s3 and s20):
            continue
        res["B_month"][mth] = {"days": s3["n"], "h3": s3["mean_pct"], "h20": s20["mean_pct"]}
        say("  %-8s %5d %+8.2f%% %+8.2f%% %+7.2f"
            % (mth, s3["n"], s3["mean_pct"], s20["mean_pct"],
               s3["mean_pct"] - s20["mean_pct"]))
    wins = sum(1 for v in res["B_month"].values() if v["h3"] > v["h20"])
    say("  h=3 优于 h=20 的月份：%d / %d" % (wins, len(res["B_month"])))

    # ── C 分类型 ──
    say("\n【C】分买点类型")
    for typ in ("1买", "2买"):
        sub = [r for r in rows if r["type"] == typ]
        if len(sub) < 20:
            continue
        res["C_type"][typ] = {}
        cells = []
        for h in (3, 5, 20):
            s = agg(sub, "h%d" % h, True)
            res["C_type"][typ]["h%d" % h] = s
            cells.append("%+6.2f%%(t=%+5.2f)" % (s["mean_pct"], s["t"] or 0) if s else "     -")
        say("  %-4s %3d 笔  h3 %s  h5 %s  h20 %s" % (typ, len({r['entry_date'] for r in sub}),
                                                    cells[0], cells[1], cells[2]))

    # ── D 成本敏感性 ──
    say("\n【D】成本敏感性：短持有的死穴是成本占比")
    say("  %-12s %8s %8s %8s %8s" % ("成本档", "h=2", "h=3", "h=5", "h=20"))
    for buy, sell, lab in COST_SCEN:
        cells = []
        rec = {}
        for h in (2, 3, 5, 20):
            vals = []
            for r in rows:
                b = P.get(r["code"])
                i = r["ei"]
                j = min(i + h, len(b["d"]) - 1)
                if j <= i:
                    continue
                vals.append((r["entry_date"],
                             net_of(b["c"][i], b["c"][j], buy, sell)))
            d = defaultdict(list)
            for dt, v in vals:
                d[dt].append(v)
            y = np.array([np.mean(v) for _, v in sorted(d.items())], float)
            rec["h%d" % h] = round(float(y.mean()) * 100, 3)
            cells.append("%+8.2f" % (y.mean() * 100))
        res["D_cost"][lab] = rec
        say("  %-12s %s" % (lab, " ".join(cells)))

    # ── E 账户层 ──
    # axis 用指数日历，取样本区间
    import glob as _g
    import pandas as pd
    dts = set()
    for p in sorted(_g.glob(os.path.join(TDX, "ts_index_daily", "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        d = d[d["ts_code"] == "000985.CSI"]
        dts.update(d["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8])
    axis = sorted(x for x in dts if "20260401" <= x <= "20261106")
    apx = {d: i for i, d in enumerate(axis)}

    from portfolio_mtm import Engine
    say("\n【E】账户层：同一套引擎、同一批信号，只换持有期 × 单票上限")
    say("  单票上限是无上限时日批等权的可执行性检验：信号少的日子预算会砸到一两只票上，")
    say("  不设上限时那段正收益等于单票重仓，不能算策略收益。")
    say("  %6s %8s %8s %8s %9s %7s %6s" %
        ("持有期", "单票上限", "累计", "cagr", "mdd", "sharpe", "util"))
    pick_cache = {}
    for h in (3, 5, 20):
        L = {}
        for r in rows:
            j = r["xi_h"].get(h)
            if j is None or r["entry_date"] not in apx:
                continue
            b = P[r["code"]]
            c0, c1 = b["c"][r["ei"]], b["c"][j]
            ret = net_of(c0, c1, 0.00075, 0.00125)
            exit_px = (1.0 + ret) * c0 * (1 + 0.00075) / (1 - 0.00125)
            ratio = build_ratio(b, r["ei"], j, c0, exit_px, axis, apx)
            L.setdefault(r["entry_date"], []).append(
                Pick(r["code"], apx[r["entry_date"]], apx[b["d"][j]], ratio, ret))
        pick_cache[h] = L
        for cap in (None, 0.10, 0.05, 0.03):
            v = Engine(axis, hold=h).run(L, per_name_cap=cap)
            key = "h%d_cap%s" % (h, "none" if cap is None else cap)
            res["E_acct"][key] = {k: v[k] for k in
                                  ("total", "cagr", "mdd", "sharpe", "util", "n_batch")}
            say("  %6d %8s %+8.2f%% %+8.2f%% %+8.2f%% %+9.2f %6.1f%%"
                % (h, "-" if cap is None else "%.0f%%" % (cap * 100), v["total"],
                   v["cagr"] or 0, v["mdd"], v["sharpe"], v["util"]))
    say("  注：util 不同时 cagr 不可横向比；这里 util 差异本身就是待测变量。")

    # ── F 1买 单独看：分类型里唯一 t>2 的格子，必须单独证 ──
    sub = [r for r in rows if r["type"] == "1买"]
    say("\n【F】1买 单独检验（%d 笔 / %d 天）—— 分类型里唯一 t>2 的格子"
        % (len(sub), len({r["entry_date"] for r in sub})))
    say("  %3s %22s %22s" % ("h", "按日批等权", "按笔等权"))
    for h in (3, 5, 20):
        s = agg(sub, "h%d" % h, True)
        p = agg(sub, "h%d" % h, False)
        res.setdefault("F_1buy", {})["h%d" % h] = {"day": s, "pick": p}
        if s and p:
            say("  %3d  %+7.2f%% t=%+5.2f drop5=%+6.2f  %+7.2f%% t=%+5.2f drop5=%+6.2f"
                % (h, s["mean_pct"], s["t"] or 0, s["drop5"] if s["drop5"] is not None else 0,
                   p["mean_pct"], p["t"] or 0, p["drop5"] if p["drop5"] is not None else 0))
    bym1 = defaultdict(list)
    for r in sub:
        bym1[r["entry_date"][:6]].append(r)
    line = []
    for mth in sorted(bym1):
        s = agg(bym1[mth], "h3", True)
        s20 = agg(bym1[mth], "h20", True)
        if s:
            res.setdefault("F_1buy_month", {})[mth] = {
                "days": s["n"], "h3": s["mean_pct"],
                "h20": s20["mean_pct"] if s20 else None}
            line.append("%s %+.2f" % (mth[4:], s["mean_pct"]))
    say("  1买 h=3 逐月：" + " | ".join(line))
    pos = sum(1 for v in res.get("F_1buy_month", {}).values() if v["h3"] > 0)
    say("  正收益月份 %d / %d" % (pos, len(res.get("F_1buy_month", {}))))

    # 选择层第二刀：1买 × sigdens 分位
    sd = np.array([r["sigdens"] for r in sub], float)
    if len(sd) > 30:
        thr = float(np.percentile(sd, 50))
        hi = [r for r in sub if r["sigdens"] >= thr]
        lo = [r for r in sub if r["sigdens"] < thr]
        say("\n【G】1买 × 信号密度（中位 %.3f 为界，选择层的第二刀）" % thr)
        for lab, grp in (("高密度", hi), ("低密度", lo)):
            s = agg(grp, "h3", True)
            s5 = agg(grp, "h5", True)
            res.setdefault("G_1buy_dens", {})[lab] = {"h3": s, "h5": s5}
            if s:
                say("  %-6s %2d 天  h3 %+7.2f%% (t=%+5.2f)  h5 %+7.2f%% (t=%+5.2f)"
                    % (lab, s["n"], s["mean_pct"], s["t"] or 0,
                       s5["mean_pct"] if s5 else 0, s5["t"] if s5 and s5["t"] else 0))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not a.json:
        print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
