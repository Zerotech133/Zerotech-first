# -*- coding: utf-8 -*-
"""sigdens / zt-hot 双口径对照：trail(移动止损8%) vs hold(持满20日)。

## 要回答的问题

2026-11-08 的 `zt_vs_sigdens_check` 在**持满口径**上得到两个刺眼数字：

  R0 全信号        +2.029%（基线）
  R1 sigdens pass  +1.057%（比基线差 0.97pp，随机 p=0.958 → 显著地差）
  R2 排除 hot      +2.327%（随机 p=0.025 → 过闸）

而 ① 的影子清单在 **trail + 2026 单窗口**上是：
  sigdens pass +0.629% vs 全信号 -0.054% → sigdens 好

**同一个 sigdens，两个口径方向相反。** 这不是小差异，它决定 ① 该不该继续养：
  - 若 sigdens 的优势只存在于 trail 口径 → 它「选日」的能力是假的，
    真正起作用的是「移动止损在 2026 年救了很多笔」，而止损是**每笔都要跑**的规则，
    与 sigdens 无关 → sigdens 该降级
  - 若两种口径同向 → 口径问题不重要，继续养样本

## 做法

同一批笔、同一个价格面板，只换出场规则，其余全部相同：
  hold_ret  = 池 ret（entry_px → exit_px，持满 20 日）
  trail_ret = sim_trail（最高价回撤 8% 即出，或持满 20 日）

每个规则 × 每种口径：日批均、随机抽同样天数的 p、分年。
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


et = _load("et3y", "entry_timing_3y.py")
pm = _load("portfolio_mtm", "portfolio_mtm.py")
cs = _load("combo_shadow", "combo_shadow.py")

TDX = et.TDX
OUT = os.path.join(ROOT, "portal", "trail_hold_regime.json")
HOLD = 20
TRAIL = 0.08
SIG_TH = 0.4843
ZT_Q = 0.70
SEED = 20261108
BOOT = 400


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = et.load_pool(since=a.since.replace("-", "")[:8])
    idx = et.load_index()
    axis = et.build_axis(rows, idx)
    say("池 %d 笔 / 日历 %d 日 %s~%s" % (len(rows), len(axis), axis[0], axis[-1]))

    # ---- 价格面板（ohlc 口径，止损要用 high/low）----
    codes = {r["code"] for r in rows}
    P = pm.load_price_panel(codes, TDX, ohlc=True)
    say("价格面板 %d 只" % len(P))

    # ---- 逐笔双口径 ----
    n_used = 0
    for r in rows:
        p = P.get(r["code"])
        r["trail_ret"] = None
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None:
            continue
        xt, px_t, why = cs.sim_trail(p, ei, r["entry_px"], hold=HOLD, trail=TRAIL)
        r["trail_ret"] = cs.net_ret(r["entry_px"], px_t)
        r["trail_why"] = why
        n_used += 1
    rows = [r for r in rows if r["trail_ret"] is not None]
    say("可算双口径 %d 笔" % n_used)

    # ---- 事前状态 ----
    f = et.load_market_struct(axis)
    sd = cs.load_ext_counts(axis)
    # sigdens 与 combo_shadow 同口径：前一日笔数 / 前 20 日均值
    sd_dens = sd.shift(1) / sd.rolling(20).mean().shift(1)

    zt_cnt = f["zt_cnt"]
    zt_q = _rolling_rank(zt_cnt, 250).shift(1)

    s_close = idx.set_index("trade_date")["close"].astype(float).reindex(axis)
    ma60 = s_close.rolling(60).mean()
    trend = (s_close > ma60).astype(float).where(ma60.notna()).shift(1)

    amt = defaultdict(float)
    for p_ in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd_read(p_)
        d = d[d["trade_date"].isin(set(axis))]
        if not len(d):
            continue
        for dt, v in d.groupby("trade_date")["amount"].sum().items():
            amt[dt] += float(v)
    a_s = pd.Series({d: amt.get(d, np.nan) for d in axis}, dtype=float)
    vol = (a_s.rolling(61).apply(lambda w: float((w[:-1] <= w[-1]).mean())
                                 if len(w) > 1 else np.nan, raw=True) >= 0.5).astype(float)
    vol = vol.where(a_s.rolling(61).count() > 1).shift(1)

    hot, sig_ok = set(), set()
    for r in rows:
        d = r["d"]
        a1, b1, q = trend.get(d, np.nan), vol.get(d, np.nan), zt_q.get(d, np.nan)
        if np.isfinite(a1) and np.isfinite(b1) and np.isfinite(q) and a1 == 1 and b1 == 1 and q >= ZT_Q:
            hot.add(d)
        s = sd_dens.get(d, np.nan)
        if np.isfinite(s) and s <= SIG_TH:
            sig_ok.add(d)

    rules = {
        "R0 全信号": lambda r: True,
        "R1 sigdens pass": lambda r: r["d"] in sig_ok,
        "R2 排除 hot": lambda r: r["d"] not in hot,
        "R3 sigdens pass 且非 hot": lambda r: r["d"] in sig_ok and r["d"] not in hot,
    }

    res = {"meta": {"pool": os.path.basename(et.POOL), "sig_th": SIG_TH, "zt_q": ZT_Q,
                    "trail": TRAIL, "hold": HOLD, "n_rows": len(rows),
                    "window": [axis[0], axis[-1]],
                    "hot_days": len(hot), "sig_pass_days": len(sig_ok)},
           "rules": {}}

    say("\n规则 / 口径对照（日批均，已扣双边成本）")
    say("  %-24s %-6s %4s %10s %8s %8s %s" % ("规则", "口径", "日数", "日批均", "日胜率", "随机p", "分年"))
    for name, fn in rules.items():
        sel = [r for r in rows if fn(r)]
        rec = {}
        for rule, fld in (("trail", "trail_ret"), ("hold", "ret")):
            b = _batch(sel, fld)
            if not b["days"]:
                continue
            p, p50, p95 = _null(rows, fld, b["days"], b["avg"])
            by_year = {}
            for yr in sorted({r["d"][:4] for r in sel}):
                sub = [r for r in sel if r["d"][:4] == yr]
                bb = _batch(sub, fld)
                by_year[yr] = {"days": bb["days"], "avg": bb["avg"]}
            rec[rule] = {"days": b["days"], "avg": b["avg"], "win": b["win"],
                         "p_vs_random": p, "null_p50": p50, "null_p95": p95,
                         "by_year": by_year}
            say("  %-24s %-6s %4d %+9.3f%% %7.1f%% %8s %s"
                % (name, rule, b["days"], b["avg"], b["win"],
                   "%.3f" % p if p is not None else "-",
                   {k: "%+.2f" % v["avg"] for k, v in by_year.items()}))
        res["rules"][name] = rec

    # ---- 结论：口径是否改变方向 ----
    say("\n口径一致性判定")
    out = {}
    for name in rules:
        rec = res["rules"].get(name) or {}
        t, h = rec.get("trail"), rec.get("hold")
        if not t or not h:
            continue
        r0t = res["rules"]["R0 全信号"]["trail"]["avg"]
        r0h = res["rules"]["R0 全信号"]["hold"]["avg"]
        dt, dh = t["avg"] - r0t, h["avg"] - r0h
        out[name] = {"vs_R0_trail": round(dt, 3), "vs_R0_hold": round(dh, 3),
                     "same_sign": bool(np.sign(dt) == np.sign(dh))}
        say("  %-24s 相对基线 trail %+.3fpp / hold %+.3fpp → %s"
            % (name, dt, dh, "同向" if out[name]["same_sign"] else "**反向**"))
    res["vs_baseline"] = out

    # 核心问题：sigdens 的优势是否只存在于 trail
    r1 = res["rules"].get("R1 sigdens pass") or {}
    if r1.get("trail") and r1.get("hold"):
        tt = r1["trail"]; hh = r1["hold"]
        res["sigdens_diagnosis"] = {
            "trail": {"avg": tt["avg"], "p": tt["p_vs_random"]},
            "hold": {"avg": hh["avg"], "p": hh["p_vs_random"]},
            "verdict": ("sigdens 的优势只在 trail 口径出现 → 起作用的是止损而非选日，"
                        "sigdens 应降级" if tt["p_vs_random"] is not None
                        and hh["p_vs_random"] is not None
                        and tt["p_vs_random"] <= 0.10 and hh["p_vs_random"] > 0.50 else
                        "两种口径方向一致 → 口径不是问题"),
        }
        say("\n结论：%s" % res["sigdens_diagnosis"]["verdict"])

    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


def pd_read(p):
    import pandas as pd
    d = pd.read_parquet(p, columns=["trade_date", "amount"])
    d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    return d


def _rolling_rank(s, win=250):
    v = s.to_numpy(float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        lo = max(0, i - win + 1)
        w = v[lo:i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(v[i]):
            out[i] = float((w <= v[i]).mean())
    return type(s)(out, index=s.index)


def _batch(rows, fld):
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r[fld])
    per = np.array([np.mean(v) for v in by.values()], float)
    if not len(per):
        return {"days": 0, "avg": None, "win": None}
    return {"days": len(per), "avg": round(float(per.mean()) * 100, 3),
            "win": round(float((per > 0).mean()) * 100, 1)}


def _null(rows, fld, n_keep, real, boot=BOOT):
    """随机抽同样天数的日 → 日批均零分布；返回 (p, p50, p95)。real 单位是 %。"""
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r[fld])
    allv = np.array([np.mean(v) for v in by.values()], float)
    if n_keep <= 0 or n_keep >= len(allv):
        return None, None, None
    rng = np.random.default_rng(SEED)
    draws = np.array([allv[rng.choice(len(allv), n_keep, replace=False)].mean()
                      for _ in range(boot)], float) * 100.0
    return (round(float((draws >= real).mean()), 4),
            round(float(np.percentile(draws, 50)), 3),
            round(float(np.percentile(draws, 95)), 3))


if __name__ == "__main__":
    main()
