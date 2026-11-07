# -*- coding: utf-8 -*-
"""环境分门槛的 3 年复核（K-RES-20261108-ENV3Y）。

背景
----
7 个月版 `env_strat_backtest.py` 结论是"升级为硬条件"（11 项检查全过，
高档日 alpha 比低档日高 +4.85%，日级 t=3.89）。但那是 2026-04~10 单边下跌
里的一段，样本只有 126 天 / 1,829 笔。必须搬到 3 年 PIT 扩展池上复核：
3 年 / 35 个月 / 35,828 笔已结算买点。

口径降级（必须先承认，否则结论是骗自己）
--------------------------------------
三因子原式：
  s_up  = min(1, 顺风占比/0.5) * 40     ← 依赖 pool 的 trend 字段
  s_vol = clamp((当日买点数/前5日均值 - 0.5)/1.0) * 30
  s_pnl = clamp((滚动30笔已平仓均值% + 5)/10) * 30

3 年池 **没有 trend / pv_state / pv_fv**（全 null），`_trend` 需要笔结构的
HH/HL，池里没有笔。所以 s_up 只能换日线代理，并做口径敏感性：
  ma   : close>MA20 且 MA20>MA60 的买点占比（最接近"上升趋势"语义）
  ma60 : close>MA60 的买点占比（更宽松）
  none : 不加 s_up，二因子归一 ×100/70（与 7 个月版 score2 完全同式）

价格口径：px = close * adj_factor（累计因子，同序列内可比，除权日不跳）。
**不能**把 px 与 entry_px 混用（那是 exit_rules_ext 踩过的 700% 鬼数坑）。

成本：3 年池 ret 是毛收益（entry_px→exit_px，未扣费）。分档比较不受影响
（各档同扣），但组合净值必须扣 0.075% + 0.125% = 0.2%。

用法：
    python tools/research/env_strat_3y.py            # 全量报告
    python tools/research/env_strat_3y.py --json     # 只落 JSON
"""
import argparse
import glob
import json
import os
import statistics as st
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(ROOT, "portal", "env3y_evidence.json")

BUY = ("1买", "2买", "3买")
COST = 0.00075 + 0.00125
PNL_WIN = 30
PNL_MIN = 5
BOOT = 10000
SEED = 20261108
MA_FAST, MA_SLOW = 20, 60


# ---------------------------------------------------------------- 数据

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
        if r.get("ret") is None or r.get("alpha") is None:
            continue
        if not r.get("detect_date") or not r.get("exit_date"):
            continue
        rows.append({
            "code": r["code"], "type": r["type"],
            "detect_date": r["detect_date"],
            "d8": r["detect_date"].replace("-", "")[:8],
            "exit_date": r["exit_date"], "exit_d8": r["exit_date"].replace("-", "")[:8],
            "ret": float(r["ret"]), "alpha": float(r["alpha"]),
            "idx_ret": float(r["idx_ret"]) if r.get("idx_ret") is not None else None,
            "days_held": r.get("days_held"),
        })
    return rows


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def load_trend_proxy(keys):
    """keys: {"code|date"} → DataFrame[ts_code, trade_date, px, ma20, ma60]。"""
    cset = {k.split("|")[0] for k in keys}

    px_parts = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["ts_code"].isin(cset)]
        if len(d):
            px_parts.append(d)
    px = pd.concat(px_parts, ignore_index=True)

    adj_parts = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_adj_factor", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d["trade_date"] = _norm(d["trade_date"])
        d = d[d["ts_code"].isin(cset)]
        if len(d):
            adj_parts.append(d)
    if adj_parts:
        a = pd.concat(adj_parts, ignore_index=True)
        px = px.merge(a, on=["ts_code", "trade_date"], how="left")
    else:
        px["adj_factor"] = 1.0
    px["adj_factor"] = px["adj_factor"].fillna(1.0)
    px["px"] = px["close"] * px["adj_factor"]

    px = px.drop_duplicates(["ts_code", "trade_date"]).sort_values(
        ["ts_code", "trade_date"])
    g = px.groupby("ts_code", sort=False)["px"]
    px["ma20"] = g.transform(lambda s: s.rolling(MA_FAST, min_periods=MA_FAST).mean())
    px["ma60"] = g.transform(lambda s: s.rolling(MA_SLOW, min_periods=MA_SLOW).mean())

    # 只留需要的 (code, date)，省内存
    px["_k"] = px["ts_code"] + "|" + px["trade_date"]
    px = px[px["_k"].isin(keys)]
    return px[["ts_code", "trade_date", "px", "ma20", "ma60"]]


# ---------------------------------------------------------------- 环境分

def load_index():
    """→ {trade_date(8位): 上证收盘}"""
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = _norm(d["trade_date"])
            out.append(d[["trade_date", "close"]])
    if not out:
        return {}
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx = idx.sort_values("trade_date")
    return dict(zip(idx["trade_date"], idx["close"].astype(float)))


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def build_env(rows, trend):
    """按日重建环境分。trend: {(code,date): (up_ma, up_ma60)}"""
    per_day = defaultdict(list)
    for r in rows:
        per_day[r["detect_date"]].append(r)
    dates = sorted(per_day)

    done = sorted(rows, key=lambda r: r["exit_d8"])

    # 已平仓序列按 exit_date 推进（因果：只用 exit_date < D）
    env = {}
    done_i = 0
    roll = []
    for i, d in enumerate(dates):
        while done_i < len(done) and done[done_i]["exit_d8"] < d.replace("-", "")[:8]:
            roll.append(done[done_i]["ret"])
            done_i += 1
        rw = roll[-PNL_WIN:]
        avg_pnl = (sum(rw) / len(rw) * 100.0) if len(rw) >= PNL_MIN else 0.0

        rs = per_day[d]
        n = len(rs)
        n_up = sum(1 for r in rs if trend.get((r["code"], r["d8"]), (False, False))[0])
        n_up60 = sum(1 for r in rs if trend.get((r["code"], r["d8"]), (False, False))[1])

        prev = [len(per_day[x]) for x in dates[max(0, i - 5):i]]
        base = (sum(prev) / len(prev)) if prev else float(n or 1)
        ratio = (n / base) if base else 1.0

        s_vol = _clamp((ratio - 0.5) / 1.0) * 30.0
        s_pnl = _clamp((avg_pnl + 5.0) / 10.0) * 30.0
        share, share60 = (n_up / n), (n_up60 / n)
        s_ma = min(1.0, share / 0.5) * 40.0
        s_ma60 = min(1.0, share60 / 0.5) * 40.0

        env[d] = {
            "n": n, "n_up": n_up, "n_up60": n_up60,
            "share_ma": round(share, 4), "share_ma60": round(share60, 4),
            "ratio": round(ratio, 4), "avg_pnl": round(avg_pnl, 3),
            "pnl_n": len(rw),
            "s_vol": round(s_vol), "s_pnl": round(s_pnl),
            "score_ma": int(round(s_ma + s_vol + s_pnl)),
            "score_ma60": int(round(s_ma60 + s_vol + s_pnl)),
            "score_none": int(round((s_vol + s_pnl) * 100.0 / 60.0)),
        }
    return env


# ---------------------------------------------------------------- 统计

def agg(rs, key):
    vals = [r[key] for r in rs if r.get(key) is not None]
    if not vals:
        return {"n": 0, "win": None, "avg": None, "med": None, "std": None}
    v = np.asarray(vals, float) * 100.0
    return {"n": len(v),
            "win": round(float((v > 0).mean() * 100), 1),
            "avg": round(float(v.mean()), 2),
            "med": round(float(np.median(v)), 2),
            "std": round(float(v.std(ddof=1)) if len(v) > 1 else 0.0, 2)}


def day_level(rs, key="alpha"):
    g = defaultdict(list)
    for r in rs:
        if r.get(key) is not None:
            g[r["detect_date"]].append(r[key])
    return [(d, len(v), float(np.mean(v)) * 100) for d, v in sorted(g.items())]


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return {"n": 0, "diff": None, "t": None, "p": None}
    t, p = sps.ttest_ind(a, b, equal_var=False)
    return {"n": len(a) + len(b), "hi_n": len(a), "lo_n": len(b),
            "diff": round(float(a.mean() - b.mean()), 3),
            "t": round(float(t), 2), "p": float(p)}


def boot_diff(hi, lo, n=BOOT, seed=SEED):
    if len(hi) < 2 or len(lo) < 2:
        return None
    rng = np.random.default_rng(seed)
    hi, lo = np.asarray(hi, float), np.asarray(lo, float)
    d = np.empty(n)
    for i in range(n):
        d[i] = (rng.choice(hi, len(hi), True).mean()
                - rng.choice(lo, len(lo), True).mean())
    return {"lo": round(float(np.percentile(d, 2.5)), 3),
            "hi": round(float(np.percentile(d, 97.5)), 3),
            "mid": round(float(d.mean()), 3),
            "p_one_sided": round(float((d <= 0).mean()), 4)}


def spearman(xs, ys):
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    if len(x) < 3:
        return None, None
    r, p = sps.spearmanr(x, y)
    return round(float(r), 3), float(p)


def band(rs, field, cuts):
    """固定阈值分档 → [(label, rows)]。"""
    edges = [-10 ** 9] + list(cuts) + [10 ** 9]
    labs = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if lo == -10 ** 9:
            lab = "<%d" % hi
        elif hi == 10 ** 9:
            lab = ">=%d" % lo
        else:
            lab = "%d-%d" % (lo, hi)
        labs.append((lab, [r for r in rs
                           if r.get(field) is not None and lo <= r[field] < hi]))
    return labs


def show_bands(bands, tag):
    print("\n  [%s]" % tag)
    print("  %-12s %6s %7s %8s %7s %8s %7s %8s"
          % ("档位", "笔数", "日数", "ret均", "ret胜", "alpha均", "alpha胜", "同期指数"))
    out = []
    for lab, rs in bands:
        a, al = agg(rs, "ret"), agg(rs, "alpha")
        nd = len({r["detect_date"] for r in rs})
        ix = [r["idx_ret"] for r in rs if r.get("idx_ret") is not None]
        ix_avg = round(float(np.mean(ix)) * 100, 2) if ix else None
        print("  %-12s %6d %7d %7s%% %7s%% %8s%% %7s%% %8s%%"
              % (lab, len(rs), nd, a["avg"], a["win"], al["avg"], al["win"], ix_avg))
        out.append({"label": lab, "n": len(rs), "days": nd,
                    "ret": a, "alpha": al, "idx_avg": ix_avg})
    return out


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    say = (lambda *a, **k: None) if args.json else print

    rows = load_pool()
    codes = {r["code"] for r in rows}
    keys = {r["code"] + "|" + r["d8"] for r in rows}
    say("池：%d 笔已结算买点，%d 只股票，%d 个交易日"
        % (len(rows), len(codes), len({r["detect_date"] for r in rows})))

    icl = load_index()
    # 净值轴必须裁到池窗口内：指数日线覆盖 2023-07~2026-11，比池早 5 个月、
    # 晚 10 天。直接用全量会把年化摊薄（实测 span 从 2.82 年虚增到 3.27 年）。
    d_lo = min(r["d8"] for r in rows)
    d_hi = max(r["exit_d8"] for r in rows)
    alldays8 = [k for k in sorted(icl) if d_lo <= k <= d_hi]
    px = load_trend_proxy(keys)
    trend = {}
    for t in px.itertuples(index=False):
        if t.ma20 is None or np.isnan(t.ma20) or np.isnan(t.ma60):
            trend[(t.ts_code, t.trade_date)] = (False, False)
        else:
            trend[(t.ts_code, t.trade_date)] = (
                bool(t.px > t.ma20 and t.ma20 > t.ma60),
                bool(t.px > t.ma60))
    hit = sum(1 for r in rows if (r["code"], r["d8"]) in trend)
    say("趋势代理命中：%d / %d = %.1f%%"
        % (hit, len(rows), hit / len(rows) * 100))

    env = build_env(rows, trend)
    for r in rows:
        e = env.get(r["detect_date"], {})
        for f in ("score_ma", "score_ma60", "score_none"):
            r[f] = e.get(f)
        r["share_ma"] = e.get("share_ma")

    n_days = len(env)
    say("环境分：%d 个有买点的交易日" % n_days)
    s = np.asarray([env[d]["score_ma"] for d in env], float)
    say("  score_ma   分布 min/med/max = %d / %d / %d"
        % (s.min(), np.median(s), s.max()))

    res = {
        "meta": {
            "n_buys": len(rows), "n_codes": len(codes), "n_days": n_days,
            "window": [min(r["detect_date"] for r in rows),
                       max(r["detect_date"] for r in rows)],
            "trend_hit_pct": round(hit / len(rows) * 100, 1),
            "cost_note": "池 ret 为毛收益，组合层另扣 0.2%",
            "score_dist": {"min": int(s.min()), "med": int(np.median(s)),
                           "max": int(s.max())},
        },
        "checks": {},
    }

    # ---------- A. 三口径分档 ----------
    for fld in ("score_ma", "score_ma60", "score_none"):
        res["bands_" + fld] = show_bands(
            band(rows, fld, [40, 60]), "%s 三档（阈值 40 / 60）" % fld)

    # ---------- B. 日级显著性（三档 → 高 vs 低） ----------
    res["day_cluster"] = {}
    for fld in ("score_ma", "score_ma60", "score_none"):
        hi_days = [d for d in env if env[d][fld] >= 60]
        lo_days = [d for d in env if env[d][fld] < 40]
        for key in ("alpha", "ret"):
            dl = dict((d, (n, v)) for d, n, v in day_level(rows, key))
            hi = [dl[d][1] for d in hi_days if d in dl]
            lo = [dl[d][1] for d in lo_days if d in dl]
            w = welch(hi, lo)
            if key == "alpha":
                w["boot"] = boot_diff(hi, lo)
            res["day_cluster"]["%s.%s" % (fld, key)] = w

    # ---------- C. 笔级 Spearman ----------
    res["spearman"] = {}
    for fld in ("score_ma", "score_ma60", "score_none"):
        for key in ("alpha", "ret"):
            v = [(r[fld], r[key]) for r in rows
                 if r.get(fld) is not None and r.get(key) is not None]
            rho, p = spearman([x for x, _ in v], [y for _, y in v])
            res["spearman"]["%s.%s" % (fld, key)] = {"rho": rho, "p": p}

    # ---------- D. beta 探测器检查 ----------
    hi_set = {d for d in env if env[d]["score_ma"] >= 60}
    lo_set = {d for d in env if env[d]["score_ma"] < 40}
    hi_idx = [r["idx_ret"] for r in rows
              if r["detect_date"] in hi_set and r.get("idx_ret") is not None]
    lo_idx = [r["idx_ret"] for r in rows
              if r["detect_date"] in lo_set and r.get("idx_ret") is not None]
    xs, ys = [], []
    for d in env:
        sub = [r["alpha"] for r in rows if r["detect_date"] == d
               and r.get("alpha") is not None]
        idxs = [r["idx_ret"] for r in rows if r["detect_date"] == d
                and r.get("idx_ret") is not None]
        if sub and idxs:
            xs.append(env[d]["score_ma"])
            ys.append(float(np.mean(sub)) * 100)
    rho_b, p_b = spearman(xs, ys)
    res["beta_check"] = {
        "score_vs_day_alpha_rho": rho_b, "p": p_b,
        "hi_idx_avg": round(float(np.mean(hi_idx)) * 100, 2) if hi_idx else None,
        "lo_idx_avg": round(float(np.mean(lo_idx)) * 100, 2) if lo_idx else None,
        "hi_days": len(hi_set), "lo_days": len(lo_set),
    }

    # ---------- D2. 环境分 vs 入场前大盘位置（证明它不是选股因子） ----------
    # beta_check 里的 hi_idx/lo_idx 是「入场后」20 日指数收益（事后）。要看环境分
    # 到底在测什么，必须用「入场前」的位置：前 20 日指数涨跌。
    ikeys = sorted(icl)
    pos = {k: i for i, k in enumerate(ikeys)}
    prev20 = {}
    for k in ikeys:
        i = pos[k]
        if i >= 20:
            prev20[k] = (icl[k] / icl[ikeys[i - 20]] - 1.0) * 100
    xs2, ys2, ys3 = [], [], []
    for d in sorted(env):
        k = d.replace("-", "")[:8]
        if k in prev20:
            xs2.append(env[d]["score_ma"])
            ys2.append(prev20[k])
            ys3.append(float(np.mean(
                [r["idx_ret"] for r in rows if r["detect_date"] == d
                 and r.get("idx_ret") is not None])) * 100
                if any(r.get("idx_ret") is not None for r in rows
                       if r["detect_date"] == d) else None)
    rho_p, p_p = spearman(xs2, ys2)
    pairs3 = [(a, b) for a, b in zip(xs2, ys3) if b is not None]
    rho_f, p_f = spearman([a for a, _ in pairs3], [b for _, b in pairs3])
    hi_prev = [prev20[d.replace("-", "")[:8]] for d in sorted(env)
               if env[d]["score_ma"] >= 60 and d.replace("-", "")[:8] in prev20]
    lo_prev = [prev20[d.replace("-", "")[:8]] for d in sorted(env)
               if env[d]["score_ma"] < 40 and d.replace("-", "")[:8] in prev20]
    res["position_check"] = {
        "score_vs_prev20_idx_rho": rho_p, "p": p_p,
        "prev20_idx_hi_avg": round(float(np.mean(hi_prev)), 2) if hi_prev else None,
        "prev20_idx_lo_avg": round(float(np.mean(lo_prev)), 2) if lo_prev else None,
        "score_vs_fwd20_idx_rho": rho_f, "p_fwd": p_f,
        "note": "环境分与入场前20日指数涨跌 rho=+0.70：它是趋势动量代理（涨过了分就高），不是独立选股能力",
    }
    say("\n  [环境分在测什么]")
    say("  score vs 入场前20日指数涨跌  rho=%s p=%.2g  （负 = 跌得越深分数越高）"
        % (rho_p, p_p if p_p is not None else -1))
    say("  高档日均前20日 %s%% vs 低档日 %s%%"
        % (res["position_check"]["prev20_idx_hi_avg"],
           res["position_check"]["prev20_idx_lo_avg"]))
    say("  score vs 入场后20日指数涨跌  rho=%s p=%.2g  （负 = 分数高则往后大盘更差）"
        % (rho_f, p_f if p_f is not None else -1))

    # ---------- D3. 三分位切分（阈值 40/60 来自小样本，3 年分布中位已到 57） ----------
    sv = np.asarray([r["score_ma"] for r in rows if r.get("score_ma") is not None],
                    float)
    q1, q2 = float(np.quantile(sv, 1 / 3)), float(np.quantile(sv, 2 / 3))
    res["quantile_cut"] = {"q33": round(q1, 1), "q67": round(q2, 1)}
    res["bands_q"] = show_bands(
        band(rows, "score_ma", [q1, q2]), "score_ma 三分位（切点 %.0f / %.0f）" % (q1, q2))
    dlq = dict((d, (n, v)) for d, n, v in day_level(rows, "alpha"))
    hiq = {r["detect_date"] for r in rows
           if r.get("score_ma") is not None and r["score_ma"] >= q2}
    loq = {r["detect_date"] for r in rows
           if r.get("score_ma") is not None and r["score_ma"] < q1}
    res["quantile_welch"] = welch([dlq[d][1] for d in hiq if d in dlq],
                                  [dlq[d][1] for d in loq if d in dlq])

    # ---------- D4. 控制大盘位置后的正交检验 ----------
    # 环境分与「入场前 20 日指数涨跌」rho=+0.70，两者高度共线。若在 prev20 同组
    # 内环境分不再有区分力，说明它没有独立信息，只是趋势动量的另一种写法。
    prev_of_day = {d: prev20[d.replace("-", "")[:8]] for d in sorted(env)
                   if d.replace("-", "")[:8] in prev20}
    pv = np.asarray(list(prev_of_day.values()), float)
    qs = [float(np.quantile(pv, x)) for x in (0.2, 0.4, 0.6, 0.8)]
    res["orthogonal"] = []
    say("\n  [正交检验：控制入场前20日大盘位置]")
    say("  %-14s %6s %8s %8s %8s %8s"
        % ("prev20 分组", "日数", "高分数", "低分数", "alpha差", "t"))
    for i in range(5):
        lo_e = -1e9 if i == 0 else qs[i - 1]
        hi_e = 1e9 if i == 4 else qs[i]
        grp = [d for d, v in prev_of_day.items() if lo_e <= v < hi_e]
        hy = [dlq[d][1] for d in grp if env[d]["score_ma"] >= 60 and d in dlq]
        ly = [dlq[d][1] for d in grp if env[d]["score_ma"] < 40 and d in dlq]
        w = welch(hy, ly)
        lab = "Q%d %s" % (i + 1, ("<%.1f%%" % qs[0]) if i == 0 else
                          (">%.1f%%" % qs[3]) if i == 4 else
                          ("%.1f~%.1f%%" % (qs[i - 1], qs[i])))
        res["orthogonal"].append({"group": lab, "days": len(grp), **w})
        say("  %-14s %6d %8s %8s %8s %8s"
            % (lab, len(grp), w.get("hi_n"), w.get("lo_n"), w["diff"], w["t"]))
    orth = [x for x in res["orthogonal"] if x["diff"] is not None]
    res["orthogonal_summary"] = {
        "n_groups": len(orth),
        "positive": sum(1 for x in orth if x["diff"] > 0),
        "sig_positive": sum(1 for x in orth if x["diff"] > 0 and x["p"] < 0.05),
        "avg_diff": round(float(np.mean([x["diff"] for x in orth])), 3) if orth else None,
    }

    # ---------- E. 年度 / 分段稳健 ----------
    res["by_year"] = {}
    for fld in ("score_ma", "score_none"):
        yy = {}
        for yr in sorted({r["detect_date"][:4] for r in rows}):
            sub = [r for r in rows if r["detect_date"][:4] == yr]
            hy = [r["alpha"] * 100 for r in sub
                  if r[fld] is not None and r[fld] >= 60]
            ly = [r["alpha"] * 100 for r in sub
                  if r[fld] is not None and r[fld] < 40]
            w = welch(hy, ly)
            w["hi_avg"] = round(float(np.mean(hy)), 2) if hy else None
            w["lo_avg"] = round(float(np.mean(ly)), 2) if ly else None
            yy[yr] = w
        res["by_year"][fld] = yy

    # ---------- F. 剔 3买 / 滞后一日 ----------
    nb = [r for r in rows if r["type"] != "3买"]
    res["excl_3buy"] = show_bands(band(nb, "score_ma", [40, 60]), "剔除3买 score_ma")

    lag = {}
    ds = sorted(env)
    prev_of = {ds[i]: (ds[i - 1] if i else None) for i in range(len(ds))}
    for r in rows:
        p = prev_of.get(r["detect_date"])
        r["score_ma_lag"] = env[p]["score_ma"] if p else None
    lag["bands"] = show_bands(band(rows, "score_ma_lag", [40, 60]),
                             "滞后一日 score_ma（用前一有买点日）")
    # 日级检验必须按「滞后档所属日集合」分组，否则等于用当日分组的 repeat
    dl = dict((d, (n, v)) for d, n, v in day_level(rows, "alpha"))
    hi_lag = {r["detect_date"] for r in rows
              if r["score_ma_lag"] is not None and r["score_ma_lag"] >= 60}
    lo_lag = {r["detect_date"] for r in rows
              if r["score_ma_lag"] is not None and r["score_ma_lag"] < 40}
    lag["welch_day"] = welch([dl[d][1] for d in hi_lag if d in dl],
                             [dl[d][1] for d in lo_lag if d in dl])
    res["lag1"] = lag

    # ---------- G. 逐日 jackknife ----------
    hi_days_alpha = {d: v for d, n, v in day_level(rows, "alpha")}
    hi_list = [d for d in env if env[d]["score_ma"] >= 60 and d in hi_days_alpha]
    lo_list = [d for d in env if env[d]["score_ma"] < 40 and d in hi_days_alpha]
    jk = []
    for drop in hi_list:
        a = [hi_days_alpha[d] for d in hi_list if d != drop]
        b = [hi_days_alpha[d] for d in lo_list]
        if len(a) > 1:
            jk.append(round(float(np.mean(a) - np.mean(b)), 3))
    res["jackknife"] = {
        "n_hi": len(hi_list), "n_lo": len(lo_list),
        "min": min(jk) if jk else None, "max": max(jk) if jk else None,
        "all_positive": bool(jk) and min(jk) > 0,
    }

    # ---------- H. 阈值扫描 ----------
    scan = []
    for th in range(20, 81, 5):
        sel = [r for r in rows if r.get("score_ma") is not None and r["score_ma"] >= th]
        if not sel:
            continue
        al = agg(sel, "alpha")
        scan.append({"th": th, "n": len(sel),
                     "cover": round(len(sel) / len(rows) * 100, 1),
                     "alpha_avg": al["avg"], "alpha_win": al["win"],
                     "ret_avg": agg(sel, "ret")["avg"]})
    res["threshold_scan"] = scan
    say("\n  [阈值扫描 score_ma ≥ th]")
    say("  %4s %7s %7s %9s %9s" % ("th", "笔数", "覆盖%", "ret均%", "alpha均%"))
    for x in scan:
        say("  %4d %7d %6s%% %8s%% %8s%%"
            % (x["th"], x["n"], x["cover"], x["ret_avg"], x["alpha_avg"]))

    # ---------- I. 日组等权净值（资金预算模型） ----------
    def nav(sel_rows, hold=20, calendar=False):
        """每日开仓 1/hold 权益，持有 hold 日。

        **收益必须在结算日入账，不能记在开仓日**——否则未来收益被提前记入净值，
        净值曲线整体前移 20 天，回撤和夏普全部失真（第一批实现就是错的）。

        calendar=True 时轴用全市场交易日历（来自指数日线），无信号日记 0。
        若不铺全日历，一是各档复利次数不同（高档 317 批次 vs 低档 164 批次）不可比，
        二是结算日落在非买点日时该笔收益会被静默丢弃——这会系统性抬高净值。
        """
        by = defaultdict(list)
        for r in sel_rows:
            by[r["detect_date"]].append((r["ret"] - COST, r["exit_d8"]))
        if not by:
            return None
        days = sorted(by)
        d8 = {d.replace("-", "")[:8] for d in days}

        # 批次 → (结算日, 该批次对权益的贡献)
        ev = defaultdict(float)
        for d in days:
            xs = by[d]
            batch = float(np.mean([x[0] for x in xs]))
            settle = sorted(x[1] for x in xs)[len(xs) // 2]
            ev[settle] += batch * (1.0 / hold)

        # 净值曲线：在交易日轴上逐日复利，结算日才记收益
        axis8 = list(alldays8) if calendar else sorted(
            {d.replace("-", "")[:8] for d in days} | set(ev))
        eq, curve, peak, mdd = 1.0, [], 1.0, 0.0
        for k in axis8:
            eq *= (1.0 + ev.get(k, 0.0))
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1.0)
            curve.append(eq)
        span = (pd.to_datetime(axis8[-1]) - pd.to_datetime(axis8[0])).days or 1
        yrs = span / 365.0
        cagr = eq ** (1 / yrs) - 1 if eq > 0 and yrs > 0 else None
        r = np.diff(np.log(np.asarray(curve)))
        sharpe = (r.mean() / r.std(ddof=1) * np.sqrt(252)) \
            if len(r) > 2 and r.std(ddof=1) > 0 else None
        return {"n": len(sel_rows), "days": len(days), "total": round((eq - 1) * 100, 2),
                "cagr": round(cagr * 100, 2) if cagr is not None else None,
                "mdd": round(mdd * 100, 2),
                "sharpe": round(float(sharpe), 2) if sharpe is not None else None,
                "span_years": round(yrs, 2)}

    def _sel(field, lo=None, hi=None):
        out = []
        for r in rows:
            v = r.get(field)
            if v is None:
                continue
            if lo is not None and v < lo:
                continue
            if hi is not None and v >= hi:
                continue
            out.append(r)
        return out

    books = (
        ("全信号", rows),
        ("score_ma>=60", _sel("score_ma", lo=60)),
        ("score_ma 40-60", _sel("score_ma", lo=40, hi=60)),
        ("score_ma<40", _sel("score_ma", hi=40)),
        ("score_none>=60", _sel("score_none", lo=60)),
        ("剔3买", [r for r in rows if r["type"] != "3买"]),
    )
    res["nav"], res["nav_cal"] = {}, {}
    for lab, sel in books:
        res["nav"][lab] = nav(sel)
        res["nav_cal"][lab] = nav(sel, calendar=True)

    idx_all = [r["idx_ret"] for r in rows if r.get("idx_ret") is not None]
    res["nav"]["基准_上证均值"] = round(float(np.mean(idx_all)) * 100, 2)

    for key, title in (("nav", "批次复利（各档批次不同，仅参考）"),
                       ("nav_cal", "日历对齐复利（675 交易日全铺，可横向比）")):
        say("\n  [日组等权净值 · %s]" % title)
        say("  %-16s %7s %7s %9s %9s %9s %7s"
            % ("口径", "笔数", "开仓日", "累计%", "年化%", "回撤%", "夏普"))
        for k, v in res[key].items():
            if isinstance(v, dict):
                say("  %-16s %7s %7s %8s%% %8s%% %8s%% %7s"
                    % (k, v["n"], v["days"], v["total"], v["cagr"], v["mdd"], v["sharpe"]))
            else:
                say("  %-16s 上证同期笔均 %s%%" % (k, v))

    # ---------- J. 判定 ----------
    c = res["checks"]
    dc = res["day_cluster"]["score_ma.alpha"]
    c["日级高档>低档"] = bool(dc["diff"] and dc["diff"] > 0)
    c["日级 p<0.05"] = bool(dc["p"] is not None and dc["p"] < 0.05)
    c["alpha bootstrap CI 不含0"] = bool(
        dc.get("boot") and dc["boot"]["lo"] > 0)
    c["逐日 jackknife 全正"] = res["jackknife"]["all_positive"]
    c["二因子口径同向"] = bool(
        res["day_cluster"]["score_none.alpha"]["diff"] is not None
        and res["day_cluster"]["score_none.alpha"]["diff"] > 0)
    c["滞后一日仍同向"] = bool(lag["welch_day"]["diff"] is not None
                          and lag["welch_day"]["diff"] > 0)
    # 非 beta 探测器：高档日的指数同期收益不能明显高于低档日，
    # 否则"高档日 alpha 高"只是"高档日大盘涨"的另一种说法。
    c["非 beta 探测器"] = bool(
        (res["beta_check"]["hi_idx_avg"] or 0)
        <= (res["beta_check"]["lo_idx_avg"] or 0) + 3)
    # 但反过来更要命：若环境分与「入场前」指数涨跌强相关，它就是个
    # 趋势动量代理（涨过了分就高），而不是独立选股能力。阈值取 |rho|<0.3。
    c["非趋势动量代理"] = bool(
        res["position_check"]["score_vs_prev20_idx_rho"] is not None
        and abs(res["position_check"]["score_vs_prev20_idx_rho"]) < 0.3)
    yrs_ok = [y for y, v in res["by_year"]["score_ma"].items()
              if v["diff"] is not None and v["diff"] > 0]
    c["年度过半同向"] = len(yrs_ok) >= 2
    # 净值是最终裁判，用日历对齐口径（各档复利次数一致）
    hi_nav = (res["nav_cal"]["score_ma>=60"] or {}).get("cagr", -999)
    lo_nav = (res["nav_cal"]["score_ma<40"] or {}).get("cagr", 999)
    c["账户层高档>低档"] = bool(hi_nav is not None and lo_nav is not None
                          and hi_nav > lo_nav)
    # 正交后是否还有增量：控制"入场前大盘位置"后，组内高/低分仍同向显著
    c["正交后仍有增量"] = bool(res["orthogonal_summary"]["sig_positive"] >= 3)
    res["pass"] = sum(1 for v in c.values() if v)
    if not c["账户层高档>低档"] and c["正交后仍有增量"]:
        res["verdict"] = "方向反转：低分档账户最优，应作「低分加仓」而非「低分禁开仓」"
    elif not c["账户层高档>低档"]:
        res["verdict"] = "撤销：纯趋势动量代理，无独立信息，作禁开仓门槛是错的"
    elif not c["非趋势动量代理"]:
        res["verdict"] = "降级：疑似趋势动量代理，需正交化后再验"
    else:
        res["verdict"] = ("维持硬条件" if res["pass"] >= 8 else
                          "降级为软加权" if res["pass"] >= 6 else "撤销")

    say("\n  [检查]")
    for k, v in c.items():
        say("  %-24s %s" % (k, "✓" if v else "✗"))
    say("  通过 %d / %d → %s" % (res["pass"], len(c), res["verdict"]))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % OUT)


if __name__ == "__main__":
    main()
