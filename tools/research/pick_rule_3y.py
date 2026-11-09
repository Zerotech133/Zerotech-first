# -*- coding: utf-8 -*-
"""限只数选股规则落产线前验证（K-RES-20261108-PICK）。

要落的规则
----------
1. 剔除 3买
2. 每个开仓日最多买 K 只（K = 20 / 25 / 30）
3. 按 type → 结构信号排序取前 K

**限 K 只为压方差，不为收益。** 上轮已证「限 K 只 + 随机选不衰减」
（K=5 +17.47% vs 全买 +16.96%），说明个股选哪只没有 alpha。既然如此，
限 K 的唯一意义是降低同时持仓数、压住方差和回撤，排序键就应该按
「低波/低 beta」这类降方差因子选，而不是按收益预期选。

排序键口径
----------
- lowvol   前 20 日日收益标准差升序（**降方差主键**）
- lowbeta  池内 beta 升序（**降方差备选**，与 lowvol 可能共线）
- bigamt   前 20 日成交额均值降序（流动性，压冲击成本）
- shortlag 确认延迟 lag 升序（信号新鲜）
- depth    depth_bars 升序 —— **伪键对照**：它是 len(bars)，等于「次新股优先」，
           没有任何缠论结构语义。上轮把它当「结构新鲜度」是错的，这里留着
           就是为了量化它到底带来多少虚假增益。
- random   随机（下限基线）

口径
----
出场统一用「持满 20 日」（池自带 exit_date/exit_px），与选股层正交。
净值走共用引擎（全交易日历 + 逐日盯市 + 现金约束），与 exit_rules_ext 同源。
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402

TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
POOL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(ROOT, "portal", "pick_rule_evidence.json")

BUY = ("1买", "2买", "3买")
HOLD = 20
SEED = 20261108
TYPE_RANK = {"1买": 0, "2买": 1, "3买": 2}


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def load_pool(drop3=False):
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
        if drop3 and r["type"] == "3买":
            continue
        rows.append({
            "code": r["code"], "type": r["type"],
            "d": r["entry_date"].replace("-", "")[:8],
            "x": (r.get("exit_date") or "").replace("-", "")[:8],
            "entry_px": float(r["entry_px"]), "exit_px": float(r["exit_px"]),
            "ret": float(r["ret"]), "alpha": r.get("alpha"),
            "idx": r.get("idx_ret"), "beta": r.get("beta"),
            "lag": r.get("lag"), "depth": r.get("depth_bars"),
            "ei": r.get("entry_i"),
        })
    return rows


def load_bars(codes):
    """因子口径必须用复权价算，否则除权跳空会污染 vol20/mom20。"""
    cs = set(codes)
    out, adj = [], []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close", "amount"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["ts_code"].isin(cs)]
        if len(d):
            out.append(d)
    bars = pd.concat(out, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["ts_code"].isin(cs)]
        if len(d):
            adj.append(d)
    if adj:
        a = pd.concat(adj, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
        bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
        af = bars["adj_factor"].to_numpy(float)
        af = np.where(np.isfinite(af) & (af > 0), af, 1.0)
    else:
        af = np.ones(len(bars))
    bars["cp"] = bars["close"].to_numpy(float) * af
    return bars.sort_values(["ts_code", "trade_date"])


def load_index():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = _norm(d["trade_date"])
            out.append(d)
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx = idx.sort_values("trade_date")
    return dict(zip(idx["trade_date"], idx["close"].astype(float)))


def add_factors(rows, bars):
    """在 entry 日之前算 PIT 因子：vol20 / amt20 / mom20。"""
    need = defaultdict(list)
    for r in rows:
        need[r["code"]].append(r["d"])
    P = {}
    for code, g in bars.groupby("ts_code", sort=False):
        if code not in need:
            continue
        dts = g["trade_date"].tolist()
        c = g["cp"].to_numpy(float)
        am = g["amount"].to_numpy(float)
        pos = {d: i for i, d in enumerate(dts)}
        P[code] = (dts, c, am, pos)
    miss = 0
    for r in rows:
        t = P.get(r["code"])
        if t is None:
            miss += 1
            continue
        dts, c, am, pos = t
        i = pos.get(r["d"])
        if i is None or i < 21:
            miss += 1
            continue
        win = c[i - 20:i]                 # 不含 entry 日，严格 PIT
        ret = np.diff(win) / win[:-1]
        r["vol20"] = float(np.std(ret, ddof=1)) if len(ret) > 2 else None
        r["mom20"] = float(c[i - 1] / c[i - 21] - 1.0) if c[i - 21] > 0 else None
        a = am[i - 20:i]
        a = a[np.isfinite(a) & (a > 0)]
        r["amt20"] = float(np.log(a.mean())) if len(a) else None
    return miss


SORTS = {
    # name: (keyfunc, 说明)
    "random": None,
    "lowvol": lambda r: (r.get("vol20") is None, r.get("vol20") or 9e9),
    "lowbeta": lambda r: (r.get("beta") is None, r.get("beta") or 9e9),
    "bigamt": lambda r: (r.get("amt20") is None, -(r.get("amt20") or -9e9)),
    "shortlag": lambda r: (r.get("lag") is None, r.get("lag") or 9e9),
    "depth": lambda r: (r.get("depth") is None, r.get("depth") or 9e9),
}


def pick_day(rows_d, k, sort, rng):
    n = len(rows_d)
    if k >= n:
        return rows_d
    if sort == "random":
        return [rows_d[i] for i in rng.choice(n, k, replace=False)]
    f = SORTS[sort]
    # 先按 type 分档，再按排序键 —— 这就是「type → 结构信号」的两级排序
    s = sorted(rows_d, key=lambda r: (TYPE_RANK.get(r["type"], 9), f(r)))
    return s[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--boot", type=int, default=20)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = load_pool(drop3=False)
    codes = {r["code"] for r in rows}
    bars = load_bars(codes)
    idxc = load_index()
    say("池 %d 笔 / %d 票；行情 %d 行" % (len(rows), len(codes), len(bars)))
    miss = add_factors(rows, bars)
    ok = sum(1 for r in rows if r.get("vol20") is not None)
    say("因子命中 %d / %d（%.1f%%），缺 %d" % (ok, len(rows), ok / len(rows) * 100, miss))

    days = sorted({r["d"] for r in rows})
    axis = [k for k in sorted(idxc) if days[0] <= k <= max(r["x"] for r in rows)]
    ap_ = {d: i for i, d in enumerate(axis)}
    eng = Engine(axis, hold=HOLD)

    # 预建每笔的 ratio（与限只数无关，只建一次）
    P = load_price_panel(codes, TDX)
    n_nocode = 0
    for r in rows:
        p = P.get(r["code"])          # 池里混了指数代码（399001.SZ 等），无行情即跳过
        if p is None:
            n_nocode += 1
            r["ratio"] = None
            continue
        ei = p["pos"].get(r["d"])
        xi = p["pos"].get(r["x"])
        if ei is None or xi is None or xi <= ei:
            r["ratio"] = None
            continue
        r["ratio"] = build_ratio(p, ei, xi, r["entry_px"], r["exit_px"], axis, ap_)
    rows = [r for r in rows if r["ratio"] is not None]
    days = sorted({r["d"] for r in rows})
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r)
    say("可回测 %d 笔 / %d 日 / 日历 %d 日（无行情 %d 笔已剔除）"
        % (len(rows), len(days), len(axis), n_nocode))

    def run(sel_rows, k, sort, rng, max_slots=None):
        b = defaultdict(list)
        for r in sel_rows:
            b[r["d"]].append(r)
        picks = {}
        for d, xs in b.items():
            chosen = pick_day(xs, k, sort, rng)
            picks[d] = [Pick(r["code"], ap_[r["d"]], ap_[r["x"]], r["ratio"], r["ret"])
                        for r in chosen]
        return eng.run(picks, max_slots=max_slots)

    res = {"meta": {"n": len(rows), "days": len(days), "axis": len(axis),
                    "window": [axis[0], axis[-1]], "hold": HOLD,
                    "factor_hit_pct": round(ok / len(rows) * 100, 1)}}

    # ---------- A. 全买基线（剔3买 vs 不剔） ----------
    say("\n" + "=" * 108)
    say("A. 基线：全买（不限只数，每日投入 1/%d 权益）" % HOLD)
    say("=" * 108)
    say("  %-18s %7s %9s %9s %9s %8s %9s"
        % ("口径", "笔数", "累计", "年化", "最大回撤", "夏普", "年化波动"))
    base = {}
    for lab, sel in (("全信号", rows),
                     ("剔3买", [r for r in rows if r["type"] != "3买"]),
                     ("仅3买", [r for r in rows if r["type"] == "3买"]),
                     ("仅1买", [r for r in rows if r["type"] == "1买"]),
                     ("仅2买", [r for r in rows if r["type"] == "2买"])):
        v = run(sel, 10 ** 9, "random", np.random.default_rng(SEED))
        base[lab] = {x: v[x] for x in ("total", "cagr", "mdd", "sharpe", "vol", "util", "n_batch")}
        base[lab]["n"] = len(sel)
        say("  %-18s %7d %8.2f%% %8.2f%% %8.2f%% %7.2f %8.2f%%"
            % (lab, len(sel), v["total"], v["cagr"] or 0, v["mdd"], v["sharpe"], v["vol"]))
    res["baseline"] = base

    # ---------- B. 排序键 × K（剔3买） ----------
    say("\n" + "=" * 108)
    say("B. 剔3买 + 限 K 只 × 排序键（随机键 200 次重抽取中位）")
    say("    看两列：最大回撤 / 年化波动 是否真的被压下来，代价是多少收益")
    say("=" * 108)
    say("  %-10s %4s %9s %8s %9s %8s %9s"
        % ("排序键", "K", "累计", "年化", "最大回撤", "夏普", "年化波动"))
    nb = [r for r in rows if r["type"] != "3买"]
    rng = np.random.default_rng(SEED)
    grid = []
    for sort in ("lowvol", "lowbeta", "bigamt", "shortlag", "depth", "random"):
        for k in (20, 25, 30) if sort != "random" else (20, 25, 30):
            if sort == "random":
                tots, mdds, shs, vols, cagrs = [], [], [], [], []
                for _ in range(a.boot):
                    v = run(nb, k, sort, rng)
                    tots.append(v["total"]); mdds.append(v["mdd"])
                    shs.append(v["sharpe"]); vols.append(v["vol"]); cagrs.append(v["cagr"])
                rec = {"sort": sort, "k": k,
                       "total": round(float(np.median(tots)), 2),
                       "cagr": round(float(np.median(cagrs)), 2),
                       "mdd": round(float(np.median(mdds)), 2),
                       "sharpe": round(float(np.median(shs)), 2),
                       "vol": round(float(np.median(vols)), 2)}
            else:
                v = run(nb, k, sort, rng)
                rec = {"sort": sort, "k": k, "total": v["total"], "cagr": v["cagr"],
                       "mdd": v["mdd"], "sharpe": v["sharpe"], "vol": v["vol"],
                       "util": v["util"], "n_drop": v["n_drop"]}
            grid.append(rec)
            say("  %-10s %4d %8.2f%% %7.2f%% %8.2f%% %7.2f %8.2f%%"
                % (sort, k, rec["total"], rec["cagr"] or 0, rec["mdd"],
                   rec["sharpe"], rec["vol"]))
    res["grid"] = grid

    # ---------- C. 降方差效果（相对全买） ----------
    say("\n" + "=" * 108)
    say("C. 降方差效果（对照剔3买全买：累计 %.2f%% / 回撤 %.2f%% / 波动 %.2f%%）"
        % (base["剔3买"]["total"], base["剔3买"]["mdd"], base["剔3买"]["vol"]))
    say("=" * 108)
    say("  %-10s %4s %10s %10s %10s %10s"
        % ("排序键", "K", "Δ收益pp", "Δ回撤pp", "Δ波动pp", "夏普变化"))
    r0 = base["剔3买"]
    cand = []
    for g in grid:
        d_t = g["total"] - r0["total"]
        d_m = g["mdd"] - r0["mdd"]      # 回撤是负数，变好 = 数值变大
        d_v = g["vol"] - r0["vol"]
        cand.append({"sort": g["sort"], "k": g["k"], "d_total": round(d_t, 2),
                     "d_mdd": round(d_m, 2), "d_vol": round(d_v, 2),
                     "sharpe": g["sharpe"]})
        say("  %-10s %4d %+9.2f %+9.2f %+9.2f %+10.2f"
            % (g["sort"], g["k"], d_t, d_m, d_v, g["sharpe"] - r0["sharpe"]))
    res["variance_delta"] = cand

    # ---------- D. 推荐方案的稳健性 ----------
    # 判据：K 在 20~30 区间内、排序键同族、回撤与波动同时改善、收益损失 < 3pp
    recs = [c for c in cand if 20 <= c["k"] <= 30 and c["d_mdd"] > 0 and c["d_vol"] < 0
            and c["d_total"] > -3.0]
    recs.sort(key=lambda c: (-c["d_mdd"], c["sharpe"]))
    res["recommend"] = recs[:6]
    say("\n" + "=" * 108)
    say("D. 满足「回撤与波动双改善 + 收益损失<3pp」的候选")
    say("=" * 108)
    if recs:
        for c in recs[:6]:
            say("  %-10s K=%-3d 收益 %+6.2fpp 回撤 %+6.2fpp 波动 %+6.2fpp 夏普 %.2f"
                % (c["sort"], c["k"], c["d_total"], c["d_mdd"], c["d_vol"], c["sharpe"]))
    else:
        say("  无候选——限只数没有在可接受代价内压住方差，规则不该落产线")

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % OUT)


if __name__ == "__main__":
    main()
