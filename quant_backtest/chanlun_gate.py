# -*- coding: utf-8 -*-
"""缠论信号实盘闸门（G1 池子 / G2 成本 / G3 集中）。

用途
----
任何新的信号 jsonl 进来，跑一遍这个脚本，5 分钟内给出「能不能进实盘讨论」
的硬结论。三条判据已在前序实证里定死，**不在这里调参**，只做判定：

  G1 池子闸  毛 alpha 的 FM 月度聚类 t ≥ 1.96 且 n ≥ 3000。
             不过 → 直接终止，G2/G3 不再作为放行依据（仅展示数值）。
  G2 成本闸  单笔毛 alpha 的 |均值| ≥ 3 × 平均往返成本（standard 档，单笔 10 万）。
  G3 集中闸  top decile 净 alpha 的 FM 月度聚类 t ≥ 1.96。
             输入无 score/预测分列 → 输出 null + not_applicable，不伪造。

FM 口径（关键，不能退化为普通 t）
--------------------------------
按 entry_date 前 7 位分月 → 每月先算均值 → 对月均值序列算 t：
    t = mean(monthly_means) / (std(monthly_means, ddof=1) / sqrt(k))
k 为月数，k < 6 返回 None。这是纠正横截面相关导致 t 虚高的口径。

数据口径
--------
- 若行内已有 `pol` 结构（见 chanlun_cost_model 输出），直接用；
- 若行内已有 px/adv/sigma/alpha 且 hold 与请求持期一致，直接用；
- 否则按第 next_open 入场、第 hold 日收盘出场重算，beta/基准走
  `chanlun_alpha.beta_of / bench_window_ret`，与 chanlun_cost_model.py 同源。
- 同票相邻信号默认按 max(holds) 个交易日去重（与成本模型一致），
  可用 --no-dedup 关闭。

用法:
    python chanlun_gate.py                                  # 全量回放 6×3
    python chanlun_gate.py --pool data/xxx.jsonl --holds 5,10,20
    python chanlun_gate.py --types 1买 --holds 10 --no-dedup
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
OUT = os.path.join(DATA, "chanlun_gate.json")
REF = os.path.join(DATA, "chanlun_cost_model.json")

TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]
HOLDS = (5, 10, 20)
NOTIONAL = 1e5          # 单笔 10 万（standard 档基准规模）
G1_T = 1.96
G1_N = 3000
G2_MULT = 3.0
G3_T = 1.96
G3_Q = 0.9              # top decile 阈值
MIN_MONTHS = 6
SCORE_KEYS = ("score", "pred", "ml_score", "rank_score", "y_score")


# ── FM 月度聚类（唯一允许的显著性口径） ────────────────────────
def fm(pairs):
    """pairs: [(entry_date, value)] → {months, avg(%), t} 或 None。"""
    by = {}
    for d, v in pairs:
        if v is None or not d:
            continue
        by.setdefault(str(d)[:7], []).append(float(v))
    if len(by) < MIN_MONTHS:
        return None
    mo = [sum(v) / len(v) for v in by.values()]
    k = len(mo)
    mu = sum(mo) / k
    var = sum((x - mu) ** 2 for x in mo) / (k - 1)
    if var <= 0:
        return {"months": k, "avg": round(mu * 100, 3), "t": None}
    return {"months": k, "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / k ** 0.5), 2)}


def _blk(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return None
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / (n - 1)
    return {"n": n, "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / n ** 0.5), 2) if var > 0 else None,
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / n, 1)}


# ── 逐票重算（与 chanlun_cost_model._one 同口径） ───────────────
def _cost_of(px, adv, sigma):
    """standard 档往返成本率（adv 单位：元）。复用 struct_ml 的实现。"""
    from chanlun_struct_ml import _cost

    return _cost(px, adv, sigma, NOTIONAL)


def _one(job):
    code, rows, holds, need, dedup = job
    import chanlun_alpha as ca
    import chanlun_scan as cs
    from chanlun_backtest import _dir_ret

    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path) or []
    if len(bars) < 130 or len(bars) <= need + 2:
        return []
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["trade_date", "amount"])
        amt_map = {cs._td_dt(t): (a if a and a > 0 else None)
                   for t, a in zip(df["trade_date"], df["amount"])}
    except Exception:
        amt_map = {}
    amt = [amt_map.get(b["dt"]) for b in bars]
    close = [b.get("c") for b in bars]

    idx = {b["dt"]: i for i, b in enumerate(bars)}
    if dedup:
        kept, last = [], -10 ** 9
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
        # 已预计算路径：px/adv/sigma/alpha/hold 齐备且持期匹配
        if all(r.get(k) is not None for k in ("px", "adv", "sigma", "alpha")):
            hs = r.get("hold")
            if hs is not None and int(hs) in holds:
                c = _cost_of(float(r["px"]), float(r["adv"]), float(r["sigma"]))
                if c is None:
                    continue
                out.append({
                    "code": code, "type": r["type"],
                    "entry_date": r.get("entry_date"),
                    "score": _score_of(r),
                    "pol": {str(int(hs)): {
                        "raw": r.get("ret"), "alpha": float(r["alpha"]),
                        "px": float(r["px"]), "adv": float(r["adv"]),
                        "sigma": float(r["sigma"]), "cost": c}},
                })
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
        cost = _cost_of(float(px), float(adv), float(sig))
        if cost is None:
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
            rec[str(h)] = {"raw": round(float(raw), 6),
                           "alpha": None if alpha is None else round(alpha, 6),
                           "px": round(float(px), 3), "adv": float(adv),
                           "sigma": round(float(sig), 5), "cost": cost}
        if rec:
            out.append({"code": code, "type": r["type"], "entry_date": ed,
                        "score": _score_of(r), "pol": rec})
    return out


def _adv_sigma(amt, close, ei, win=20):
    """入场前 win 日 ADV（元）与日收益标准差；与 chanlun_cost_model 同式。"""
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


def _score_of(r):
    for k in SCORE_KEYS:
        if r.get(k) is not None:
            try:
                return float(r[k])
            except (TypeError, ValueError):
                return None
    return None


# ── 三闸判定 ──────────────────────────────────────────────────
def judge(cells, hold, has_score):
    """cells: 同一 type×hold 的信号列表 → 单格判定 dict。"""
    hs = str(hold)
    sub = [c for c in cells if hs in c["pol"]]
    pairs = [(c["entry_date"], c["pol"][hs]["alpha"]) for c in sub]
    f_gross = fm(pairs)
    n = len([1 for _, v in pairs if v is not None])

    gross = _blk([v for _, v in pairs])
    costs = [c["pol"][hs]["cost"] for c in sub if c["pol"][hs]["cost"] is not None]
    cost_avg = (sum(costs) / len(costs)) if costs else None

    # G1 池子闸
    g1_t = (f_gross or {}).get("t")
    g1_ok = bool(g1_t is not None and g1_t >= G1_T and n >= G1_N)

    # G2 成本闸（不论 G1 是否通过都算出来，便于定位）
    g_mean = (gross or {}).get("avg")
    ratio = (abs(g_mean) / 100.0 / cost_avg) if (g_mean is not None and cost_avg) else None
    g2_ok = bool(ratio is not None and ratio >= G2_MULT)

    # G3 集中闸
    if not has_score:
        g3 = {"status": "not_applicable", "t": None, "pass": None,
              "note": "输入无 score/预测分列，不伪造"}
    else:
        scored = [c for c in sub
                  if c.get("score") is not None and c["pol"][hs]["alpha"] is not None]
        if len(scored) < 50:
            g3 = {"status": "insufficient_score", "t": None, "pass": None,
                  "n": len(scored)}
        else:
            sv = sorted(c["score"] for c in scored)
            cut = sv[min(len(sv) - 1, int(len(sv) * G3_Q))]
            top = [c for c in scored if c["score"] >= cut]
            net = [(c["entry_date"],
                    c["pol"][hs]["alpha"] - c["pol"][hs]["cost"]) for c in top]
            f_net = fm(net)
            g3 = {"status": "evaluated", "n": len(top),
                  "avg": round(sum(v for _, v in net if v is not None)
                               / max(1, len([v for _, v in net if v is not None])) * 100, 3),
                  "t": (f_net or {}).get("t"), "months": (f_net or {}).get("months")}
            g3["pass"] = bool(g3["t"] is not None and g3["t"] >= G3_T)

    if not g1_ok:
        stuck, verdict = "G1", "reject"
    elif not g2_ok:
        stuck, verdict = "G2", "reject"
    elif g3.get("pass") is False:
        stuck, verdict = "G3", "reject"
    else:
        stuck, verdict = None, "pass"

    return {
        "hold": hold, "n": n,
        "g1": {"fm_t": g1_t, "months": (f_gross or {}).get("months"),
               "n": n, "n_min": G1_N, "t_min": G1_T, "pass": g1_ok},
        "g2": {"gross_alpha_pct": g_mean, "cost_pct": None if cost_avg is None
               else round(cost_avg * 100, 3),
               "cost_thresh_pct": None if cost_avg is None
               else round(cost_avg * 100 * G2_MULT, 3),
               "ratio": None if ratio is None else round(ratio, 2),
               "mult": G2_MULT, "pass": g2_ok},
        "g3": g3,
        "stuck_at": stuck, "verdict": verdict,
        "gross_stats": gross,
    }


def _reference_check(report, ref_path=REF):
    """与既有成本模型结论交叉核对（±0.05pp）。"""
    if not os.path.exists(ref_path):
        return {"status": "skipped", "reason": "no reference file"}
    try:
        ref = json.load(open(ref_path, encoding="utf-8"))["main"]["types"]
    except Exception as exc:
        return {"status": "skipped", "reason": str(exc)}
    out = {}
    for t, hs in (("1买", "10"), ("1买", "20"), ("2买", "20"), ("3买", "10")):
        cell = report["types"].get(t, {}).get(hs)
        rb = ref.get(t, {}).get(hs)
        if not cell or not rb:
            continue
        std = ((rb.get("net") or {}).get("standard") or {}).get(str(int(NOTIONAL))) or {}
        mine = {"gross_alpha": cell["g2"]["gross_alpha_pct"],
                "fm_t": cell["g1"]["fm_t"],
                "cost": cell["g2"]["cost_pct"],
                "net_alpha": None if (cell["g2"]["gross_alpha_pct"] is None or
                                      cell["g2"]["cost_pct"] is None)
                else round(cell["g2"]["gross_alpha_pct"] - cell["g2"]["cost_pct"], 3)}
        theirs = {"gross_alpha": (rb.get("gross_alpha") or {}).get("avg"),
                  "fm_t": (rb.get("fm_gross") or {}).get("t"),
                  "cost": std.get("cost_avg"),
                  "net_alpha": (std.get("net_alpha") or {}).get("avg")}
        d = {}
        for k in mine:
            a, b = mine[k], theirs[k]
            if a is None or b is None:
                d[k] = None
            else:
                d[k] = round(float(a) - float(b), 3)
        ok = all(v is not None and abs(v) <= 0.05 for k, v in d.items() if k != "fm_t")
        out["%s_%s" % (t, hs)] = {"mine": mine, "ref": theirs, "diff": d,
                                  "within_0.05pp": ok}
    out["status"] = "ok" if out else "skipped"
    return out


# ── 主流程 ────────────────────────────────────────────────────
def run(pool=DEFAULT_POOL, holds=HOLDS, types=None, workers=6, dedup=True,
        limit=0, quiet=False):
    holds = [int(h) for h in holds]
    need = max(holds)
    types = types or TYPES
    rows = [json.loads(li) for li in open(pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r.get("type") in types]
    n_raw = len(rows)
    has_score = any(any(r.get(k) is not None for k in SCORE_KEYS) for r in rows)
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if limit:
        codes = codes[:limit]
    t0 = time.time()
    if not quiet:
        print("pool=%s 信号=%d 标的=%d holds=%s dedup=%s need=%d score=%s"
              % (os.path.basename(pool), n_raw, len(codes), holds, dedup, need,
                 has_score))

    res = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(_one, [(c, by_code[c], holds, need, dedup)
                                  for c in codes], chunksize=4):
            res.extend(part)
    elapsed = time.time() - t0
    if not quiet:
        print("算完 %d 笔，用时 %.0fs" % (len(res), elapsed))
    if not res:
        return {"status": "no_sample"}

    agg = {}
    for r in res:
        agg.setdefault(r["type"], []).append(r)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool": os.path.basename(pool), "holds": holds, "dedup": dedup,
        "need_bars": need, "types": {}, "elapsed_s": round(elapsed, 1),
        "workers": workers, "notional": NOTIONAL, "cost_preset": "standard",
        "entry": "next_open", "exit": "close@hold",
        "fm": "月度聚类: entry_date[:7] 均值序列 t, k<%d -> null" % MIN_MONTHS,
        "gates": {"G1": {"t_min": G1_T, "n_min": G1_N},
                  "G2": {"mult": G2_MULT},
                  "G3": {"t_min": G3_T, "quantile": G3_Q,
                         "score": "present" if has_score else "absent"}},
        "n_signals_used": len(res), "has_score": has_score,
    }
    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        report["types"][t] = {}
        for h in holds:
            report["types"][t][str(h)] = judge(lst, h, has_score)

    cells = [c for t in report["types"].values() for c in t.values()]
    report["summary"] = {
        "n_cells": len(cells),
        "n_reject": sum(1 for c in cells if c["verdict"] == "reject"),
        "stuck_at": {g: sum(1 for c in cells if c["stuck_at"] == g)
                     for g in ("G1", "G2", "G3")},
        "all_rejected": all(c["verdict"] == "reject" for c in cells),
        "all_stuck_at_g1": all(c["stuck_at"] == "G1" for c in cells),
        "verdict": ("不可进实盘讨论：全部卡在 G1 池子闸"
                    if all(c["verdict"] == "reject" for c in cells) else "存在通过格"),
    }
    report["reference_check"] = _reference_check(report)
    return report


def print_table(report):
    print("\n%-4s %-4s %6s %8s %9s %8s %9s %7s  %-6s %s"
          % ("信号", "持期", "n", "毛alpha%", "FM_t(G1)", "成本%", "成本阈值%",
             "倍数", "卡点", "G3"))
    print("-" * 96)
    for t, blk in report["types"].items():
        for h, c in blk.items():
            g1, g2, g3 = c["g1"], c["g2"], c["g3"]
            print("%-4s %-4s %6d %+9.3f %9s %8s %9s %7s  %-6s %s"
                  % (t, h, c["n"], g2["gross_alpha_pct"] or 0,
                     "-" if g1["fm_t"] is None else "%.2f" % g1["fm_t"],
                     "-" if g2["cost_pct"] is None else "%.3f" % g2["cost_pct"],
                     "-" if g2["cost_thresh_pct"] is None
                     else "%.3f" % g2["cost_thresh_pct"],
                     "-" if g2["ratio"] is None else "%.2f" % g2["ratio"],
                     c["stuck_at"] or "通过",
                     g3["status"] if g3["status"] != "evaluated"
                     else "t=%s" % g3["t"]))


def main():
    ap = argparse.ArgumentParser(description="缠论信号实盘三闸门")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--types", default=",".join(TYPES))
    ap.add_argument("--holds", default=",".join(str(h) for h in HOLDS))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dedup", action="store_true", default=True)
    ap.add_argument("--no-dedup", dest="dedup", action="store_false")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    holds = [int(x) for x in a.holds.replace(" ", "").split(",") if x]
    types = [t.strip() for t in a.types.split(",") if t.strip()]
    report = run(a.pool, holds, types, a.workers, a.dedup, a.limit)
    if report.get("status") == "no_sample":
        print("无可算样本，未写文件")
        return

    print_table(report)
    print("\n汇总：%s" % json.dumps(report["summary"], ensure_ascii=False))
    rc = report.get("reference_check") or {}
    if rc.get("status") == "ok":
        print("核对 chanlun_cost_model.json：")
        for k, v in rc.items():
            if k == "status":
                continue
            print("  %-8s mine=%s ref=%s diff=%s %s"
                  % (k, v["mine"], v["ref"], v["diff"],
                     "OK" if v["within_0.05pp"] else "**偏差>0.05pp**"))

    with open(a.out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(a.out + ".tmp", a.out)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
