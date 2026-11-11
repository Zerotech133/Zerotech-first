# -*- coding: utf-8 -*-
"""持仓期敏感性：HOLD_DAYS=20 的 alpha 是不是「窗口挑出来的」。

问题
----
三年 PIT 扩样本（次日开盘入场）给出 1买 alpha +0.43%(t=3.25)，2买 +0.21%
(t=0.98)，3买 -0.30%(t=-2.77)。但 20 个交易日这个持期本身是产线写死的
常量，若 alpha 只在 20 日附近为正、两侧迅速衰减，那它更像是「刚好截到
某段反弹」，不能作为策略依据。

做法
----
入场统一为次日开盘，只换持期 hold ∈ HOLDS（默认 5/10/15/20/30/40/60）。
**统一信号集**：只有最长持期也能算完的信号才进样本，各 hold 完全配对，
差异全部来自持期本身，不是样本漂移。

输出
----
data/chanlun_hold_sensitivity.json —— 每类型 × hold 的 n/raw/alpha/t/win，
以及相对 hold=20 的配对差（Δalpha, 配对 t, 改善占比）。

用法:
    python chanlun_hold_sensitivity.py
    python chanlun_hold_sensitivity.py --holds 5,10,20,40 --limit 400
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
DEFAULT_POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(DATA, "chanlun_hold_sensitivity.json")
HOLDS = (5, 10, 15, 20, 30, 40, 60)
BASE = 20


def _one(job):
    code, rows, holds, need, dedup = job
    import chanlun_alpha as ca
    import chanlun_scan as cs
    from chanlun_backtest import _dir_ret

    bars = cs._bars_from_parquet(
        os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    ) or []
    if len(bars) < 130 or len(bars) <= need + 2:
        return []
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    if dedup:  # 同票相邻信号至少间隔 need 个交易日，消除重叠样本对 t 的高估
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
        d0 = idx.get(r.get("detect_date"))
        if d0 is None or d0 + 1 >= len(bars):
            continue
        ei = d0 + 1
        px = bars[ei].get("o")
        if not px or px <= 0 or ei + need >= len(bars):
            continue
        ed = bars[ei]["dt"]
        beta, est, _ = ca.beta_of(code, ed)
        # 入场前动量（控制变量：检验信号是否只是「近期涨幅」的代理）
        pre20 = None
        if d0 - 20 >= 0 and bars[d0 - 20]["c"]:
            pre20 = round(bars[d0]["c"] / bars[d0 - 20]["c"] - 1.0, 4)
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
            rec[str(h)] = {
                "raw": round(float(raw), 6),
                "alpha": None if br is None else round(float(raw) - beta * br, 6),
            }
        if rec:
            out.append({"code": code, "type": r["type"], "sig_dt": r["dt"],
                        "entry_date": ed, "pre20": pre20, "pol": rec})
    return out


def _fm(rows, hs):
    """Fama-MacBeth：按入场月份聚成月度均值后再算 t（横截面相关的正确判据）。"""
    by = {}
    for r in rows:
        e = r.get("pol", {}).get(hs) or {}
        v = e.get("alpha")
        if v is None or not r.get("entry_date"):
            continue
        by.setdefault(r["entry_date"][:7], []).append(v)
    if len(by) < 6:
        return None
    mo = [sum(v) / len(v) for v in by.values()]
    mu = sum(mo) / len(mo)
    var = sum((x - mu) ** 2 for x in mo) / (len(mo) - 1)
    return {"months": len(mo), "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / len(mo) ** 0.5), 2) if var > 0 else None}


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


def _paired(a_vals, b_vals):
    d = [y - x for x, y in zip(a_vals, b_vals) if x is not None and y is not None]
    n = len(d)
    if n < 2:
        return None
    mu = sum(d) / n
    var = sum((v - mu) ** 2 for v in d) / (n - 1)
    return {"n": n, "diff": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None,
            "better": round(sum(1 for v in d if v > 0) * 100.0 / n, 1)}


def main():
    ap = argparse.ArgumentParser(description="持仓期敏感性（配对）")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--types", default="1买,2买,3买,1卖,2卖,3卖")
    ap.add_argument("--holds", default=",".join(str(h) for h in HOLDS))
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--dedup",
        action="store_true",
        help="同票相邻信号至少间隔 max(holds) 个交易日（消除重叠样本，t 值不被高估）",
    )
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    holds = [int(x) for x in a.holds.replace(" ", "").split(",") if x]
    need = max(holds)
    types = [t.strip() for t in a.types.split(",") if t.strip()]
    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r["type"] in types]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if a.limit:
        codes = codes[: a.limit]
    print("pool=%s 信号=%d 标的=%d holds=%s%s（统一信号集需最长持期可算）"
          % (os.path.basename(a.pool), len(rows), len(codes), holds,
             " dedup" if a.dedup else ""))

    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(
            _one,
            [(c, by_code[c], holds, need, a.dedup) for c in codes],
            chunksize=4,
        ):
            res.extend(part)
    print("算完 %d 笔，用时 %.0fs" % (len(res), time.time() - t0))

    agg = {}
    for r in res:
        agg.setdefault(r["type"], []).append(r)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "pool": os.path.basename(a.pool), "holds": holds, "dedup": a.dedup,
           "base": BASE, "entry": "next_open", "n_signals": len(res),
           "note": "统一信号集：各 hold 完全配对，差异只来自持期"
                   + ("；dedup=同票间隔≥最长持期" if a.dedup else ""),
           "types": {}}

    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        blk = {}
        for h in holds:
            hs = str(h)
            blk[hs] = {
                "raw": _blk([r["pol"][hs]["raw"] for r in lst if hs in r["pol"]]),
                "alpha": _blk([r["pol"][hs]["alpha"] for r in lst if hs in r["pol"]]),
                "fm": _fm(lst, hs),
            }
        bh = str(BASE)
        if bh in blk:
            for h in holds:
                hs = str(h)
                if hs == bh:
                    continue
                pairs = [(r["pol"][bh]["alpha"], r["pol"][hs]["alpha"])
                         for r in lst
                         if hs in r["pol"] and bh in r["pol"]]
                blk[hs]["vs_base_alpha"] = _paired(
                    [x for x, _ in pairs], [y for _, y in pairs])
        # 分年（按入场年）：检验是否某一年独撑
        blk["by_year"] = {}
        for h in holds:
            hs = str(h)
            yy = {}
            for r in lst:
                if hs not in r["pol"]:
                    continue
                y = r["entry_date"][:4]
                yy.setdefault(y, []).append(r["pol"][hs]["alpha"])
            blk["by_year"][hs] = {y: _blk(v) for y, v in sorted(yy.items())}
        # 按入场前 20 日涨幅三分组：检验信号是否只是「近期涨幅」的代理
        blk["by_pre20"] = {}
        pool_p = sorted(r["pre20"] for r in lst if r.get("pre20") is not None)
        if len(pool_p) >= 30:
            lo, hi = pool_p[len(pool_p) // 3], pool_p[2 * len(pool_p) // 3]
            for h in holds:
                hs = str(h)
                g = {"low": [], "mid": [], "high": []}
                for r in lst:
                    if hs not in r["pol"] or r.get("pre20") is None:
                        continue
                    p = r["pre20"]
                    k = "low" if p <= lo else ("high" if p > hi else "mid")
                    g[k].append(r["pol"][hs]["alpha"])
                blk["by_pre20"][hs] = {k: _blk(v) for k, v in g.items()}
        out["types"][t] = blk

        print("\n[%s] n=%d" % (t, len(lst)))
        print("  %5s %8s %7s %6s %8s %7s %8s %7s %9s %7s"
              % ("持期", "raw%", "t_raw", "win%", "alpha%", "t_alp",
                 "FM_t", "FM月", "Δalpha", "Δ_t"))
        for h in holds:
            hs = str(h)
            b = blk[hs]
            rw, al = b["raw"], b["alpha"]
            if not rw:
                continue
            pa = b.get("vs_base_alpha") or {}
            fm = b.get("fm") or {}
            print("  %5d %+8.2f %7s %6.1f %+8.2f %7s %8s %7s %+9s %7s"
                  % (h, rw["avg"], rw["t"], rw["win"],
                     al["avg"] if al else 0, (al or {}).get("t"),
                     fm.get("t", "-"), fm.get("months", "-"),
                     pa.get("diff", "-"), pa.get("t", "-")))

    with atomic_io.replacing(a.out) as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
