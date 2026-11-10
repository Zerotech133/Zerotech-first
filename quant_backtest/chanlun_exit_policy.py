# -*- coding: utf-8 -*-
"""退出政策对照实验：卖点信号还该不该当离场依据（P0）。

背景
----
三年扩样本（逐日重放，65,964 笔）中，卖点信号作为**独立信号**的 alpha
显著为负：1卖 -1.62%(t=-6.74)、2卖 -2.13%(t=-6.56)、3卖 -0.26%(t=-1.66)。

但这不能直接推出「用它当离场触发器也亏」：作为独立信号，它承担的是
「方向押注」；作为离场触发器，它真正起作用的是「相对满期退出的配对差」。
两者口径不同，必须单独验证，否则就是拿错证据改产线。

设计
----
入场统一为**次日开盘**（与产线 ENTRY_MODE=next_open 一致），只换退出：

  A_hold20      满 HOLD 个交易日按收盘离场（基线）
  B_rev_any     任一反向类别信号检出日收盘离场，否则 hold20（产线现行）
  C_rev_1sell   仅 1卖 触发，否则 hold20
  D_rev_23sell  仅 2卖/3卖 触发，否则 hold20
  E_rev_1buy    仅 1买 触发（供卖点仓位用）

买点仓位（1买/2买/3买）比较 A/B/C/D；卖点仓位（方向统计）比较 A/B/E。
只有基线与目标政策**同时可算**的信号才进配对差，避免样本漂移。

输出
----
data/chanlun_exit_policy_eval.json —— 每类型 × 政策：n/raw/alpha/t/win，
以及相对 A 的配对均值差（alpha 与 raw 各一份）与配对 t。

用法:
    python chanlun_exit_policy.py                      # 全量 daily 扩样本
    python chanlun_exit_policy.py --limit 600          # 抽样快跑
    python chanlun_exit_policy.py --types 1买,2买,3买
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import atomic_io  # noqa: E402  (P2-c 统一原子写)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
HOLD = 20
DEFAULT_POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(DATA, "chanlun_exit_policy_eval.json")

BUY_POLICIES = ("A_hold20", "B_rev_any", "C_rev_1sell", "D_rev_23sell")
SELL_POLICIES = ("A_hold20", "B_rev_any", "E_rev_1buy")


def _is_buy(t):
    return str(t).endswith("买")


def _one(job):
    """单票：为该票所有信号算各退出政策的 (raw, alpha)。"""
    code, rows, hold = job
    import chanlun_alpha as ca
    import chanlun_scan as cs
    from chanlun_backtest import _dir_ret

    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path) or []
    if len(bars) < 130:
        return []
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    # 反向触发器：该票全部池内信号（含卖点），按确认日索引排序
    marks = sorted(
        (idx[r["detect_date"]], _is_buy(r["type"]), r["type"])
        for r in rows
        if r.get("detect_date") in idx
    )
    out = []
    bwin = {}
    for r in rows:
        t = r["type"]
        d0 = idx.get(r.get("detect_date"))
        if d0 is None or d0 + 1 >= len(bars):
            continue
        ei = d0 + 1  # 次日开盘入场
        entry_px = bars[ei].get("o")
        if not entry_px or entry_px <= 0:
            continue
        base_exit = ei + hold
        if base_exit >= len(bars):  # 保证基线可算，否则各政策样本不一致
            continue
        buy = _is_buy(t)
        ed = bars[ei]["dt"]

        def first_rev(pred):
            for j, mbuy, mty in marks:
                if j > ei and mbuy != buy and pred(mty):
                    return min(j, base_exit)
            return base_exit

        pol = {
            "A_hold20": base_exit,
            "B_rev_any": first_rev(lambda ty: True),
            "C_rev_1sell": first_rev(lambda ty: ty == "1卖"),
            "D_rev_23sell": first_rev(lambda ty: ty in ("2卖", "3卖")),
            "E_rev_1buy": first_rev(lambda ty: ty == "1买"),
        }
        beta, est, npair = ca.beta_of(code, ed)
        rec = {}
        for name, xi in pol.items():
            if xi is None or xi <= ei:
                continue
            xd = bars[xi]["dt"]
            raw = _dir_ret(t, entry_px, bars[xi]["c"])
            if raw is None:
                continue
            k = (ed, xd)
            if k not in bwin:
                bwin[k] = ca.bench_window_ret(ed, xd)
            br = bwin[k]
            al = None if br is None else raw - beta * br
            rec[name] = {
                "raw": round(float(raw), 6),
                "alpha": None if al is None else round(float(al), 6),
                "exit": xd,
                "days": xi - ei,
            }
        if "A_hold20" not in rec:
            continue
        out.append(
            {"code": code, "type": t, "sig_dt": r["dt"],
             "detect_date": r["detect_date"], "entry_date": ed,
             "beta": beta if est else None, "pol": rec}
        )
    return out


def _blk(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return None
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / (n - 1)
    t = round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None
    s = sorted(vals)
    return {"n": n, "avg": round(mu * 100, 3), "t": t,
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / n, 1),
            "med": round(s[n // 2] * 100, 3)}


def _paired(vals_a, vals_b):
    """配对差 b - a（同信号），返回均值差与配对 t。"""
    d = [y - x for x, y in zip(vals_a, vals_b) if x is not None and y is not None]
    n = len(d)
    if n < 2:
        return None
    mu = sum(d) / n
    var = sum((v - mu) ** 2 for v in d) / (n - 1)
    return {"n": n, "diff": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None,
            "better": round(sum(1 for v in d if v > 0) * 100.0 / n, 1)}


def main():
    ap = argparse.ArgumentParser(description="卖点信号作为离场依据的对照实验")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--types", default="1买,2买,3买,1卖,2卖,3卖")
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（抽样）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    types = [t.strip() for t in a.types.split(",") if t.strip()]
    rows = [r for r in rows if r["type"] in types and r.get("status", "closed") == "closed"]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if a.limit:
        codes = codes[: a.limit]
    print("pool=%s 类型=%s 信号=%d 标的=%d hold=%d"
          % (os.path.basename(a.pool), ",".join(types), len(rows), len(codes), a.hold))

    t0 = time.time()
    jobs = [(c, by_code[c], a.hold) for c in codes]
    res = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, part in enumerate(ex.map(_one, jobs, chunksize=4)):
            res.extend(part)
            if (i + 1) % 500 == 0:
                print("  %d/%d 票  %d 信号  %.0fs"
                      % (i + 1, len(jobs), len(res), time.time() - t0))
    print("算完 %d 笔，用时 %.0fs" % (len(res), time.time() - t0))

    # 统计：按类型 × 政策
    agg = {}
    for r in res:
        agg.setdefault(r["type"], []).append(r)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "pool": os.path.basename(a.pool), "hold": a.hold,
           "n_signals": len(res),
           "entry": "next_open", "types": {}}

    print("\n=== 每类型 × 退出政策（入场=次日开盘）===")
    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        pols = BUY_POLICIES if _is_buy(t) else SELL_POLICIES
        blk = {}
        for p in pols:
            raws = [r["pol"][p]["raw"] for r in lst if p in r["pol"]]
            als = [r["pol"][p]["alpha"] for r in lst if p in r["pol"]]
            days = [r["pol"][p]["days"] for r in lst if p in r["pol"]]
            blk[p] = {
                "raw": _blk(raws), "alpha": _blk(als),
                "days_med": sorted(days)[len(days) // 2] if days else None,
                "coverage": round(len(raws) * 100.0 / len(lst), 1),
            }
        # 配对差：仅 A 与 p 同时可算的信号
        for p in pols[1:]:
            pairs = [(r["pol"]["A_hold20"], r["pol"][p])
                     for r in lst if p in r["pol"] and "A_hold20" in r["pol"]]
            blk[p]["vs_hold20_raw"] = _paired(
                [x["raw"] for x, _ in pairs], [y["raw"] for _, y in pairs])
            blk[p]["vs_hold20_alpha"] = _paired(
                [x["alpha"] for x, _ in pairs], [y["alpha"] for _, y in pairs])
        out["types"][t] = blk

        print("\n[%s] n=%d" % (t, len(lst)))
        print("  %-13s %6s %8s %7s %6s %8s %7s %6s %8s %7s %6s"
              % ("政策", "n", "raw%", "t_raw", "win%", "alpha%", "t_alp",
                 "覆盖%", "Δalpha", "Δ_t", "Δ胜%"))
        for p in pols:
            b = blk[p]
            rw, al = b["raw"], b["alpha"]
            if not rw:
                continue
            pa = b.get("vs_hold20_alpha") or {}
            print("  %-13s %6d %+8.2f %7s %6.1f %+8.2f %7s %6.1f %+8s %7s %6s"
                  % (p, rw["n"], rw["avg"], rw["t"], rw["win"],
                     al["avg"] if al else 0, (al or {}).get("t"),
                     b["coverage"], pa.get("diff", "-"), pa.get("t", "-"),
                     pa.get("better", "-")))

    with atomic_io.replacing(a.out) as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
