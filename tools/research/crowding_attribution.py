# -*- coding: utf-8 -*-
"""扎堆效应归因 + 可实现性检验（K-RES-20261108-CROWD）。

问题
----
同一个 3 年池、同一个 hold20、同一个「每日投 1/hold 权益」模型，出现了两个差
26pp 的年化：
  env_strat_3y 日组等权  +16.97%（累计）
  exit_rules_ext A 规则   -9.08%（累计）

本文要回答的是三件事，按顺序钉死：
  1. 26pp 差在哪？（笔均 vs 日组均的权重差 = 协方差）
  2. 扎堆效应长什么形状？是不是单调「信号多→质量差」
  3. **那 18pp 到底能不能落地**——日组等权要求「买下当日全部信号」
     （53 只/天，极端日 1464 只），一旦限 K 只，随机选会不会退回笔均

第 3 点是关键。如果限 K 只后收益归零，那「扎堆效应是账户层杀手」这个说法要
反过来写：不是扎堆拖累了你，是**日组等权的 +17% 本身是账面权重游戏**，
笔均 −0.03% 才是账户层能拿到的真实期望。

口径
----
- 池 ret 为毛收益，一律扣 0.2%（买 0.075% + 卖 0.125%）
- 资金模型：每个开仓日投入 1/hold 权益，日内候选等权，持有 hold=20 日，
  收益按结算日入账，无信号日记 0，净值轴为全市场交易日历（与 env_strat_3y 一致）
- 限 K 只时，当日候选 < K 则全买

用法
----
    python tools/research/crowding_attribution.py
    python tools/research/crowding_attribution.py --json
"""
import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(ROOT, "portal", "crowding_evidence.json")

BUY = ("1买", "2买", "3买")
COST = 0.00075 + 0.00125
HOLD = 20
BOOT = 100
SEED = 20261108
TYPE_RANK = {"1买": 0, "2买": 1, "3买": 2}


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def load_pool():
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        rows.append({
            "code": r["code"], "type": r["type"],
            "d": r["entry_date"], "d8": r["entry_date"].replace("-", "")[:8],
            "x8": (r.get("exit_date") or "").replace("-", "")[:8],
            "ret": float(r["ret"]) - COST,
            "alpha": float(r["alpha"]) if r.get("alpha") is not None else None,
            "idx": float(r["idx_ret"]) if r.get("idx_ret") is not None else None,
            "depth": r.get("depth_bars"), "beta": r.get("beta"),
            "lag": r.get("lag"), "bei": r.get("bei"),
        })
    return rows


def load_cal():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            out.append(_norm(d["trade_date"]))
    if not out:
        return []
    return sorted(set(pd.concat(out, ignore_index=True).astype(str)))


def nav(batches, axis, hold=HOLD):
    """batches: {open_date8: [(ret, settle8), ...]} → 净值指标。"""
    if not batches:
        return None
    ev = defaultdict(float)
    for d, xs in batches.items():
        settle = sorted(x[1] for x in xs)[len(xs) // 2]
        ev[settle] += float(np.mean([x[0] for x in xs])) * (1.0 / hold)
    eq, curve, peak, mdd = 1.0, [], 1.0, 0.0
    for k in axis:
        eq *= (1.0 + ev.get(k, 0.0))
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
        curve.append(eq)
    yrs = (pd.to_datetime(axis[-1]) - pd.to_datetime(axis[0])).days / 365.0
    cagr = eq ** (1 / yrs) - 1 if eq > 0 and yrs > 0 else None
    r = np.diff(np.log(np.asarray(curve)))
    sharpe = (r.mean() / r.std(ddof=1) * np.sqrt(252)) \
        if len(r) > 2 and r.std(ddof=1) > 0 else None
    return {"total": round((eq - 1) * 100, 2),
            "cagr": round(cagr * 100, 2) if cagr is not None else None,
            "mdd": round(mdd * 100, 2),
            "sharpe": round(float(sharpe), 2) if sharpe is not None else None}


def pick(rows_d, k, rule, rng):
    """从当日候选中选 k 只。rule 为选择规则名。"""
    n = len(rows_d)
    if k >= n:
        return rows_d
    if rule == "random":
        idx = rng.choice(n, k, replace=False)
        return [rows_d[i] for i in idx]
    if rule == "type":       # 1买 > 2买 > 3买
        s = sorted(rows_d, key=lambda r: TYPE_RANK.get(r["type"], 9))
    elif rule == "depth":    # depth_bars 小 = 结构新
        s = sorted(rows_d, key=lambda r: (r["depth"] is None, r["depth"] or 1e9))
    elif rule == "beta":     # beta 低 = 抗跌
        s = sorted(rows_d, key=lambda r: (r["beta"] is None, r["beta"] or 1e9))
    elif rule == "lag":      # 确认延迟短 = 信号新
        s = sorted(rows_d, key=lambda r: (r["lag"] is None, r["lag"] or 1e9))
    elif rule == "type_depth":
        s = sorted(rows_d, key=lambda r: (TYPE_RANK.get(r["type"], 9),
                                          r["depth"] is None, r["depth"] or 1e9))
    else:
        raise ValueError(rule)
    return s[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = load_pool()
    cal = load_cal()
    days = sorted({r["d8"] for r in rows})
    axis = [k for k in cal if days[0] <= k <= max(r["x8"] for r in rows)]
    by = defaultdict(list)
    for r in rows: by[r["d8"]].append(r)

    res = {"meta": {"n": len(rows), "days": len(days), "axis": len(axis),
                    "window": [days[0], days[-1]], "cost": COST, "hold": HOLD}}

    # ---------- A. 笔均 vs 日组均：协方差分解 ----------
    ns = np.array([len(by[d]) for d in days], float)
    means = np.array([np.mean([r["ret"] for r in by[d]]) for d in days], float)
    per_trade = float(np.mean([r["ret"] for r in rows]))
    per_day = float(means.mean())
    cov = float(np.cov(ns, means)[0, 1])
    res["decomp"] = {
        "per_trade_pct": round(per_trade * 100, 4),
        "per_day_pct": round(per_day * 100, 4),
        "gap_pct": round((per_day - per_trade) * 100, 4),
        "cov_n_mean": round(cov, 6),
        "cov_predicted_gap_pct": round(-cov / ns.mean() * 100, 4),
        "n_days_mean": round(float(ns.mean()), 1),
        "n_days_median": int(np.median(ns)),
        "n_max": int(ns.max()),
    }
    say("=" * 100)
    say("A. 笔均 vs 日组均（扣 0.2% 成本）")
    say("=" * 100)
    say("  笔均        %+.4f%%" % (per_trade * 100))
    say("  日组均      %+.4f%%" % (per_day * 100))
    say("  差          %+.4f%%" % ((per_day - per_trade) * 100))
    say("  Cov(笔数,批收益) = %.6f → 预测差 %+.4f%%（应约等于实际差）"
        % (cov, -cov / ns.mean() * 100))
    say("  日内笔数 均值%.1f 中位%d 最大%d"
        % (ns.mean(), np.median(ns), ns.max()))

    # ---------- B. 日内笔数分档 ----------
    bins = [(1, 10), (10, 30), (30, 60), (60, 100), (100, 300), (300, 10 ** 9)]
    res["by_crowd"] = []
    say("\n" + "=" * 100)
    say("B. 按日内信号数分档（扎堆效应的形状）")
    say("=" * 100)
    say("  %-12s %6s %7s %9s %9s %9s %9s"
        % ("日内笔数", "日数", "笔数", "日批均", "日内笔均", "alpha均", "同期上证"))
    for lo, hi in bins:
        ds = [d for d in days if lo <= len(by[d]) < hi]
        if not ds:
            continue
        dm = float(np.mean([np.mean([r["ret"] for r in by[d]]) for d in ds]))
        pm = float(np.mean([r["ret"] for d in ds for r in by[d]]))
        al = [r["alpha"] for d in ds for r in by[d] if r["alpha"] is not None]
        ix = [r["idx"] for d in ds for r in by[d] if r["idx"] is not None]
        nb = sum(len(by[d]) for d in ds)
        rec = {"bin": "%d-%d" % (lo, min(hi, 10 ** 9 - 1)), "days": len(ds), "n": nb,
               "per_day": round(dm * 100, 2), "per_trade": round(pm * 100, 2),
               "alpha": round(float(np.mean(al)) * 100, 2) if al else None,
               "idx": round(float(np.mean(ix)) * 100, 2) if ix else None}
        res["by_crowd"].append(rec)
        say("  %-12s %6d %7d %8.2f%% %8.2f%% %8.2f%% %8.2f%%"
            % (rec["bin"], rec["days"], nb, rec["per_day"], rec["per_trade"],
               rec["alpha"] or 0, rec["idx"] or 0))

    # ---------- C. 限 K 只：随机选（可实现性基线） ----------
    say("\n" + "=" * 100)
    say("C. 限 K 只 + 随机选（%d 次重抽）：能落地的下限" % BOOT)
    say("=" * 100)
    say("  %4s %10s %10s %10s %10s %8s"
        % ("K", "累计中位", "累计5%", "累计95%", "年化中位", "夏普中位"))
    rng = np.random.default_rng(SEED)
    res["limit_random"] = []
    for k in (3, 5, 10, 20, 30, 50, 100):
        tots, cagrs, shs = [], [], []
        for _ in range(BOOT):
            b = {}
            for d in days:
                sel = pick(by[d], k, "random", rng)
                b[d] = [(r["ret"], r["x8"]) for r in sel]
            v = nav(b, axis)
            tots.append(v["total"]); cagrs.append(v["cagr"]); shs.append(v["sharpe"])
        rec = {"k": k, "total_med": round(float(np.median(tots)), 2),
               "total_lo": round(float(np.percentile(tots, 5)), 2),
               "total_hi": round(float(np.percentile(tots, 95)), 2),
               "cagr_med": round(float(np.median(cagrs)), 2),
               "sharpe_med": round(float(np.median(shs)), 2)}
        res["limit_random"].append(rec)
        say("  %4d %9.2f%% %9.2f%% %9.2f%% %9.2f%% %8.2f"
            % (k, rec["total_med"], rec["total_lo"], rec["total_hi"],
               rec["cagr_med"], rec["sharpe_med"]))

    # ---------- D. 限 K 只：按规则选 ----------
    say("\n" + "=" * 100)
    say("D. 限 K 只 + 按规则选（单次，不用重抽）")
    say("=" * 100)
    say("  %-12s %4s %10s %10s %10s %8s"
        % ("规则", "K", "累计", "年化", "最大回撤", "夏普"))
    res["limit_rule"] = []
    for rule in ("type", "depth", "beta", "lag", "type_depth"):
        for k in (5, 10, 20, 50):
            b = {d: [(r["ret"], r["x8"]) for r in pick(by[d], k, rule, rng)]
                 for d in days}
            v = nav(b, axis)
            rec = {"rule": rule, "k": k, **v}
            res["limit_rule"].append(rec)
            say("  %-12s %4d %9.2f%% %9.2f%% %9.2f%% %8.2f"
                % (rule, k, v["total"], v["cagr"], v["mdd"], v["sharpe"]))

    # ---------- E. 参照：全买（日组等权）与极端日剔除 ----------
    b_all = {d: [(r["ret"], r["x8"]) for r in by[d]] for d in days}
    v_all = nav(b_all, axis)
    res["reference"] = {"all": v_all}
    say("\n" + "=" * 100)
    say("E. 参照口径")
    say("=" * 100)
    say("  全买（日组等权，仅账面）        %+8.2f%%  年化 %6.2f%%  回撤 %7.2f%%  夏普 %5.2f"
        % (v_all["total"], v_all["cagr"], v_all["mdd"], v_all["sharpe"]))

    for th in (100, 200, 300):
        keep = [d for d in days if len(by[d]) < th]
        b = {d: [(r["ret"], r["x8"]) for r in by[d]] for d in keep}
        v = nav(b, axis)
        res["reference"]["drop_ge_%d" % th] = v
        say("  剔除 ≥%d 笔的极端日（剩 %3d 日）  %+8.2f%%  年化 %6.2f%%  回撤 %7.2f%%  夏普 %5.2f"
            % (th, len(keep), v["total"], v["cagr"], v["mdd"], v["sharpe"]))

    # 笔数加权对照：**这不是任何可实现策略**。真实组合总有「每日预算」，
    # 因此必然是日等权（每开仓日投 1/hold，日内等分），不可能按笔数加权。
    # 这里只做一件事：把同一个复利框架里的「日组均」换成「全局笔均」，
    # 用来量化 0.518pp 的均值差在 675 次复利后会放大成多少——即协方差
    # （扎堆效应）的账面量级，不代表任何可执行收益。
    pt = float(np.mean([r["ret"] for r in rows]))
    eq_pt = 1.0
    for _ in days:
        eq_pt *= (1.0 + pt / HOLD)
    res["reference"]["counterfactual_per_trade"] = {
        "per_trade_pct": round(pt * 100, 4),
        "total": round((eq_pt - 1) * 100, 2),
        "note": "假想对照：把日组均换成全局笔均后同一框架的累计，非可实现策略",
    }
    c_pt = res["reference"]["counterfactual_per_trade"]
    say("  假想·全局笔均代入同框架        %+8.2f%%  （笔均 %+.4f%%，不可实现，仅示协方差量级）"
        % (c_pt["total"], c_pt["per_trade_pct"]))

    # ---------- F. 可实现性诊断 ----------
    # 全买口径下每只股票拿到的权益比例：1/hold/n_d。按 A 股最小 100 股算最小资金。
    slots = [1.0 / HOLD / len(by[d]) for d in days]
    res["feasibility"] = {
        "weight_per_stock_med_pct": round(float(np.median(slots)) * 100, 4),
        "weight_per_stock_max_pct": round(float(np.max(slots)) * 100, 4),
        "trades_total": len(rows),
        "note": "全买口径下单只平均只占 1/hold/n 权益；按 100 股最小手，资金小时无法等权",
    }
    say("\n  [可实现性] 全买口径下单只权益占比 中位 %.4f%%、最大 %.4f%%；总交易 %d 笔"
        % (res["feasibility"]["weight_per_stock_med_pct"],
           res["feasibility"]["weight_per_stock_max_pct"], len(rows)))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % OUT)


if __name__ == "__main__":
    main()
