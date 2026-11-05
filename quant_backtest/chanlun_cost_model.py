# -*- coding: utf-8 -*-
"""交易成本模型：1买 10 日的 alpha +0.50% 扣完成本还剩多少。

为什么必须做
------------
前面所有结论都是**毛收益**口径：佣金、印花税、滑点、冲击成本一概没扣。
1买 10 日的毛 alpha 只有 +0.50%（FM t=0.86），而 A 股一次完整往返的
显性成本就在 0.10% 上下，加上滑点与冲击很容易翻到 0.30%~0.50%。
这个量级足以把全部超额吃干净——所以这是唯一能决定「要不要继续」的一步。

成本拆解（全部按「占成交金额的比例」计，往返合计）
------------------------------------------------
1) 佣金      双边收取，券商万 1.5 ~ 万 3，**有 5 元最低**（小单实际费率被抬高）
2) 印花税    仅卖出，2023-08-28 起 0.05%（样本 2024-01 之后，全在此档）
3) 过户费    双边 0.001%（2022-04 起沪深统一）
4) 滑点      单边 ≈ max(floor, k × 0.01 / price)：0.01 元是 A 股最小报价单位，
             低价股的相对 tick 成本天然更高；集合竞价成交也吃不满理论价
5) 冲击成本  Almgren 平方根律 impact = k × σ_d × sqrt(Q / ADV)
             Q = 单笔金额，ADV = 入场前 20 日日均成交额，σ_d = 20 日日收益波动

口径说明
--------
- 成本是**绝对拖累**：net_alpha = alpha - cost（alpha 已扣 beta，成本再扣一次）
- 卖点方向收益取反，但成本永远是正的（做空 A 股个股不现实，本模型只对
  买点给出净收益；卖点仅作对照，成本仍按往返全额扣）
- 出场价沿用产线口径（第 hold 日收盘），未额外假设延迟成交

用法:
    python chanlun_cost_model.py                      # 默认三档 × 五个规模
    python chanlun_cost_model.py --holds 5,10,20
    python chanlun_cost_model.py --notionals 5e4,1e5,5e5
    python chanlun_cost_model.py --types 1买 --detail  # 打印每笔成本分布
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
DEFAULT_POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(DATA, "chanlun_cost_model.json")

# ── 费率预设 ────────────────────────────────────────────────
# comm      佣金费率（双边各收一次）
# comm_min  单边最低佣金（元）
# stamp     印花税（仅卖出）
# transfer  过户费（双边各收一次）
# slip_k    滑点 tick 系数（单边，相对 0.01/price）
# slip_floor滑点下限（单边）
# impact_k  冲击成本系数（单边，平方根律）
PRESETS = {
    # 下界：只算显性费用，滑点冲击全 0 —— 物理上不可能达到，只作参照
    "friction": {"comm": 0.00025, "comm_min": 5.0, "stamp": 0.0005,
                 "transfer": 0.00001, "slip_k": 0.0, "slip_floor": 0.0,
                 "impact_k": 0.0},
    # 乐观：大客户费率 + 小单，滑点冲击取低值
    "minimal": {"comm": 0.00015, "comm_min": 5.0, "stamp": 0.0005,
                "transfer": 0.00001, "slip_k": 0.5, "slip_floor": 0.0001,
                "impact_k": 0.3},
    # 基准：万 2.5 + 半 tick 滑点 + 常规冲击（默认口径）
    "standard": {"comm": 0.00025, "comm_min": 5.0, "stamp": 0.0005,
                 "transfer": 0.00001, "slip_k": 0.5, "slip_floor": 0.0002,
                 "impact_k": 0.6},
    # 保守：万 3 + 一 tick 滑点 + 加倍冲击（散户实际体验）
    "conservative": {"comm": 0.0003, "comm_min": 5.0, "stamp": 0.0005,
                     "transfer": 0.00001, "slip_k": 1.0, "slip_floor": 0.0003,
                     "impact_k": 1.0},
}
DEFAULT_PRESETS = ("friction", "minimal", "standard", "conservative")
DEFAULT_NOTIONALS = (5e4, 1e5, 3e5, 1e6, 3e6)


def cost_rate(price, notional, adv, sigma_d, preset):
    """往返总成本率（小数，0.0035 = 0.35%）。

    price   成交价（元）
    notional单笔金额（元）
    adv     入场前 20 日日均成交额（元）
    sigma_d 20 日日收益标准差（小数）
    """
    p = PRESETS[preset] if isinstance(preset, str) else preset
    if notional <= 0:
        return None
    # 1) 佣金：双边各一次，单边最低 5 元
    comm_side = max(notional * p["comm"], p["comm_min"])
    comm = 2.0 * comm_side / notional
    # 2) 印花税：仅卖出
    stamp = p["stamp"]
    # 3) 过户费：双边
    transfer = 2.0 * p["transfer"]
    # 4) 滑点：双边，按 tick 相对价格
    tick = 0.01 / price if price and price > 0 else 0.0
    slip_side = max(p["slip_floor"], p["slip_k"] * tick)
    slip = 2.0 * slip_side
    # 5) 冲击：双边平方根律
    part = (notional / adv) if adv and adv > 0 else 0.0
    impact_side = p["impact_k"] * (sigma_d or 0.0) * math.sqrt(max(part, 0.0))
    impact = 2.0 * impact_side
    return {
        "comm": comm, "stamp": stamp, "transfer": transfer,
        "slip": slip, "impact": impact,
        "total": comm + stamp + transfer + slip + impact,
        "participation": part,
    }


def _adv_sigma(amt, close, ei, win=20):
    """入场前 win 日：ADV（元）与日收益标准差。amount 单位是千元。"""
    lo = max(0, ei - win)
    a = [x for x in amt[lo:ei] if x and x > 0]
    adv = (sum(a) / len(a) * 1000.0) if a else None
    px = [x for x in close[lo:ei + 1] if x and x > 0]
    if len(px) < 3:
        return adv, None
    rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px)) if px[i - 1]]
    if len(rets) < 2:
        return adv, None
    return adv, statistics.pstdev(rets)


def _one(job):
    code, rows, holds, need, dedup, presets, notionals, types = job
    import chanlun_alpha as ca
    import chanlun_scan as cs
    from chanlun_backtest import _dir_ret

    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path) or []
    if len(bars) < 130 or len(bars) <= need + 2:
        return []
    # amount 不在 bars 里，单独按位置对齐读一次
    try:
        import pandas as pd

        df = pd.read_parquet(path)[["trade_date", "amount"]]
    except Exception:
        return []
    amt_map = {cs._td_dt(t): (a if a and a > 0 else None)
               for t, a in zip(df["trade_date"], df["amount"])}
    amt = [amt_map.get(b["dt"]) for b in bars]
    close = [b.get("c") for b in bars]

    idx = {b["dt"]: i for i, b in enumerate(bars)}
    if dedup:
        kept, last = [], -10**9
        for r in sorted(rows, key=lambda x: x.get("detect_date") or ""):
            i0 = idx.get(r.get("detect_date"))
            if i0 is None or i0 - last < need:
                continue
            last = i0
            kept.append(r)
        rows = kept

    out = []
    bwin = {}
    for r in rows:
        if r["type"] not in types:
            continue
        d0 = idx.get(r.get("detect_date"))
        if d0 is None or d0 + 1 >= len(bars):
            continue
        ei = d0 + 1
        px = bars[ei].get("o")
        if not px or px <= 0 or ei + need >= len(bars):
            continue
        ed = bars[ei]["dt"]
        beta, _, _ = ca.beta_of(code, ed)
        adv, sig = _adv_sigma(amt, close, ei)
        if not adv or not sig:
            continue
        rec = {}
        for h in holds:
            xi = ei + h
            xd = bars[xi]["dt"]
            raw = _dir_ret(r["type"], px, bars[xi]["c"])
            if raw is None:
                continue
            k = (ed, xd)
            if k not in bwin:
                bwin[k] = ca.bench_window_ret(ed, xd)
            br = bwin[k]
            alpha = None if br is None else float(raw) - beta * br
            row = {"raw": round(float(raw), 6),
                   "alpha": None if alpha is None else round(alpha, 6),
                   "px": round(float(px), 3),
                   "adv": round(float(adv) / 1e6, 1),   # 百万元
                   "sigma": round(float(sig), 5),
                   "cost": {}}
            for ps in presets:
                row["cost"][ps] = {}
                for nt in notionals:
                    c = cost_rate(px, nt, adv, sig, ps)
                    if c is None:
                        continue
                    row["cost"][ps][str(int(nt))] = {
                        "t": round(c["total"], 6),
                        "c": round(c["comm"], 6), "s": round(c["slip"], 6),
                        "i": round(c["impact"], 6), "f": round(c["stamp"], 6),
                        "p": round(c["participation"], 6),
                    }
            rec[str(h)] = row
        if rec:
            out.append({"code": code, "type": r["type"], "sig_dt": r["dt"],
                        "entry_date": ed, "pol": rec})
    return out


def _blk(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return None
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / (n - 1)
    return {"n": n, "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None,
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / n, 1)}


def _fm(rows, hs, key="alpha", sub=0.0):
    """月度聚类 t（横截面相关的正确判据）。sub = 每期固定扣除的成本率。"""
    by = {}
    for r in rows:
        e = r.get("pol", {}).get(hs) or {}
        v = e.get(key)
        if v is None or not r.get("entry_date"):
            continue
        by.setdefault(r["entry_date"][:7], []).append(v - sub)
    if len(by) < 6:
        return None
    mo = [sum(v) / len(v) for v in by.values()]
    mu = sum(mo) / len(mo)
    var = sum((x - mu) ** 2 for x in mo) / (len(mo) - 1)
    return {"months": len(mo), "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / len(mo) ** 0.5), 2) if var > 0 else None}


def _fm_costed(rows, hs, preset, nt):
    """按每笔自有成本扣减后的月度聚类（成本随票、随价变化时的正确做法）。

    额外给出 95% 置信区间与「要使 t 达到 1.96 所需的成本上限」，
    后者是判断「再优化成本能不能救活」的直接依据（负值=物理不可能）。
    """
    by = {}
    for r in rows:
        e = r.get("pol", {}).get(hs) or {}
        a = e.get("alpha")
        cc = (e.get("cost") or {}).get(preset, {}).get(str(int(nt)))
        if a is None or not cc or not r.get("entry_date"):
            continue
        by.setdefault(r["entry_date"][:7], []).append(a - cc["t"])
    if len(by) < 6:
        return None
    mo = [sum(v) / len(v) for v in by.values()]
    mu = sum(mo) / len(mo)
    var = sum((x - mu) ** 2 for x in mo) / (len(mo) - 1)
    se = (var ** 0.5 / len(mo) ** 0.5) if var > 0 else None
    return {"months": len(mo), "avg": round(mu * 100, 3),
            "t": round(mu / se, 2) if se else None,
            "ci_lo": round((mu - 1.96 * se) * 100, 3) if se else None,
            "ci_hi": round((mu + 1.96 * se) * 100, 3) if se else None,
            "se": round(se * 100, 3) if se else None}


def _fm_breakeven(rows, hs):
    """毛 alpha 的月度聚类均值与标准误 → 要让净 alpha 的 t 达到 1.96，
    需要把往返成本压到多少（负值 = 无论如何都不可能显著）。"""
    f = _fm(rows, hs)
    if not f or f.get("t") in (None, 0):
        return None
    avg = f["avg"]
    se = abs(avg / f["t"]) if f["t"] else None
    if not se:
        return None
    need = avg - 1.96 * se  # 允许的最大成本（百分点）
    return {"gross_avg": avg, "se": round(se, 3),
            "max_cost_for_sig": round(need, 3),
            "note": "负值 = 即使零成本也不显著" if need < 0 else "可承受"}


def _by_year(rows, hs, preset, nt):
    """分年净 alpha：检验三年均值是否由某一年独撑。"""
    yy = {}
    for r in rows:
        e = r.get("pol", {}).get(hs) or {}
        a = e.get("alpha")
        cc = (e.get("cost") or {}).get(preset, {}).get(str(int(nt)))
        if a is None or not cc or not r.get("entry_date"):
            continue
        yy.setdefault(r["entry_date"][:4], []).append(a - cc["t"])
    return {y: _blk(v) for y, v in sorted(yy.items())}


def main():
    ap = argparse.ArgumentParser(description="交易成本模型：毛 alpha → 净 alpha")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--types", default="1买,2买,3买,1卖,2卖,3卖")
    ap.add_argument("--holds", default="5,10,20")
    ap.add_argument("--presets", default=",".join(DEFAULT_PRESETS))
    ap.add_argument("--notionals", default=",".join(str(int(x)) for x in DEFAULT_NOTIONALS))
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dedup", action="store_true", default=True)
    ap.add_argument("--no-dedup", dest="dedup", action="store_false")
    ap.add_argument("--detail", action="store_true", help="打印各票成本分位")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    holds = [int(x) for x in a.holds.replace(" ", "").split(",") if x]
    presets = [x.strip() for x in a.presets.split(",") if x.strip()]
    notionals = [float(x) for x in a.notionals.replace(" ", "").split(",") if x]
    types = [t.strip() for t in a.types.split(",") if t.strip()]
    need = max(holds)

    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r["type"] in types]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if a.limit:
        codes = codes[: a.limit]
    print("pool=%s 信号=%d 标的=%d holds=%s%s"
          % (os.path.basename(a.pool), len(rows), len(codes), holds,
             " dedup" if a.dedup else ""))

    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(
            _one,
            [(c, by_code[c], holds, need, a.dedup, presets, notionals, types)
             for c in codes],
            chunksize=4,
        ):
            res.extend(part)
    print("算完 %d 笔，用时 %.0fs" % (len(res), time.time() - t0))
    if not res:
        print("无可算样本")
        return

    agg = {}
    for r in res:
        agg.setdefault(r["type"], []).append(r)

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool": os.path.basename(a.pool), "holds": holds, "dedup": a.dedup,
        "entry": "next_open", "exit": "close@hold", "n_signals": len(res),
        "presets": presets, "notionals": [int(x) for x in notionals],
        "preset_params": {k: PRESETS[k] for k in presets},
        "note": "net = gross - cost（往返全额）；alpha 已扣 beta，成本另扣一次",
        "types": {},
    }

    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        blk = {}
        for h in holds:
            hs = str(h)
            cells = [r["pol"][hs] for r in lst if hs in r["pol"]]
            if not cells:
                continue
            b = {
                "n": len(cells),
                "gross_raw": _blk([c["raw"] for c in cells]),
                "gross_alpha": _blk([c["alpha"] for c in cells]),
                "fm_gross": _fm(lst, hs),
                "breakeven": _fm_breakeven(lst, hs),
                # 成本统计（标准档、基准规模），后面按 preset 覆盖
                "px_med": round(statistics.median([c["px"] for c in cells]), 2),
                "adv_med_m": round(statistics.median([c["adv"] for c in cells]), 1),
                "net": {},
            }
            for ps in presets:
                b["net"][ps] = {}
                for nt in notionals:
                    key = str(int(nt))
                    costs = [(c.get("cost") or {}).get(ps, {}).get(key) for c in cells]
                    tv = [x["t"] for x in costs if x]
                    if not tv:
                        continue
                    avg_c = sum(tv) / len(tv)
                    nr = [c["raw"] - (c.get("cost") or {}).get(ps, {})[key]["t"]
                          for c in cells]
                    na = [c["alpha"] - (c.get("cost") or {}).get(ps, {})[key]["t"]
                          for c in cells
                          if c["alpha"] is not None]
                    b["net"][ps][key] = {
                        "cost_avg": round(avg_c * 100, 3),
                        "cost_p10": round(sorted(tv)[len(tv) // 10] * 100, 3),
                        "cost_p90": round(sorted(tv)[len(tv) * 9 // 10] * 100, 3),
                        "comm": round(sum(x["c"] for x in costs if x) / len(tv) * 100, 3),
                        "slip": round(sum(x["s"] for x in costs if x) / len(tv) * 100, 3),
                        "impact": round(sum(x["i"] for x in costs if x) / len(tv) * 100, 3),
                        "fee": round(sum(x["f"] for x in costs if x) / len(tv) * 100, 3),
                        "net_raw": _blk(nr),
                        "net_alpha": _blk(na),
                        "fm_net": _fm_costed(lst, hs, ps, nt),
                        "by_year": _by_year(lst, hs, ps, nt),
                        "annual_drag": round(avg_c * 100 * 250.0 / h, 2),
                    }
            blk[hs] = b
        out["types"][t] = blk

        # ── 打印 ────────────────────────────────────────────
        print("\n" + "=" * 78)
        print("[%s]  n=%d  入场价中位 %.2f 元  ADV 中位 %.0f 万"
              % (t, blk[str(holds[0])]["n"],
                 blk[str(holds[0])]["px_med"],
                 blk[str(holds[0])]["adv_med_m"] * 100))
        for h in holds:
            hs = str(h)
            if hs not in blk:
                continue
            b = blk[hs]
            g_raw, g_al = b["gross_raw"], b["gross_alpha"]
            print("\n  持期 %d 日   毛 raw %+.2f%%   毛 alpha %+.2f%%   毛FM_t %s"
                  % (h, g_raw["avg"], (g_al or {}).get("avg", 0),
                     (b["fm_gross"] or {}).get("t", "-")))
            be = b.get("breakeven") or {}
            if be:
                print("  盈亏平衡：毛alpha月均 %.3f%% SE %.3f → 要让净alpha达到 t=1.96，"
                      "往返成本须 ≤ %.3f%%%s"
                      % (be["gross_avg"], be["se"], be["max_cost_for_sig"],
                         "（负值：零成本也不可能显著）"
                         if be["max_cost_for_sig"] < 0 else ""))
            print("  %-12s %8s %8s %9s %8s %8s %8s %8s %8s"
                  % ("成本档", "规模", "成本%", "净raw%", "净alpha%", "FM_t",
                     "佣金", "滑点", "冲击"))
            for ps in presets:
                for nt in notionals:
                    key = str(int(nt))
                    c = (b["net"].get(ps) or {}).get(key)
                    if not c:
                        continue
                    print("  %-12s %8s %+8.3f %+8.2f %+9.2f %8s %8.3f %8.3f %8.3f"
                          % (ps, "%.0f万" % (nt / 1e4), c["cost_avg"],
                             (c["net_raw"] or {}).get("avg", 0),
                             (c["net_alpha"] or {}).get("avg", 0),
                             (c["fm_net"] or {}).get("t", "-"),
                             c["comm"], c["slip"], c["impact"]))
                print()

    # 汇总：判断「还有没有净收益」的一页纸
    summary = {"generated_at": out["generated_at"], "cells": []}
    for t in types:
        if t not in out["types"]:
            continue
        for h in holds:
            b = out["types"][t].get(str(h))
            if not b:
                continue
            for ps in presets:
                for nt in notionals:
                    key = str(int(nt))
                    c = (b["net"].get(ps) or {}).get(key)
                    if not c:
                        continue
                    summary["cells"].append({
                        "type": t, "hold": h, "preset": ps, "notional": int(nt),
                        "gross_alpha": (b["gross_alpha"] or {}).get("avg"),
                        "cost": c["cost_avg"],
                        "net_alpha": (c["net_alpha"] or {}).get("avg"),
                        "net_alpha_t": (c["net_alpha"] or {}).get("t"),
                        "fm_t": (c["fm_net"] or {}).get("t"),
                        "annual_drag": c["annual_drag"],
                    })
    with open(a.out + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"main": out, "summary": summary}, f, ensure_ascii=False, indent=1)
    os.replace(a.out + ".tmp", a.out)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
