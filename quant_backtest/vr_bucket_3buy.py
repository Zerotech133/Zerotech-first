# -*- coding: utf-8 -*-
"""T1 · 三买×缩量交叉验证（研发计划 2026-11-11 R1）。

问题：《交易思路 v3.7》与 108 课双重强调三买要配「量能最低价最低」（缩量回踩）。
本脚本用全窗+近窗两窗的 3买 事件缓存，按信号日量比 vr 分桶，看后续 20 日超额
（个股 20 日收益 − 当日全市场等权 20 日收益），并做市值分层复核（防 vr 只是流动性代理）。

口径：
- 前瞻收益 = prod(1+pct_chg[t+1..t+20]) − 1（pct_chg 已含除权除息，天然无拆分失真）
- 超额 = 个股 fwd20 − 全市场当日 fwd20 等权均值（全市场 = data/ashare 全部可交易票）
- 桶：<0.7 / 0.7-1.0 / 1.0-1.5 / >1.5（+_BEI_SPLIT 同源的量能档位）
- 市值分层：事件日 circ_mv（daily_basic）中位数切大小两半，分桶结论须两半同向
- 窗口尾部 fwd20 不足 20 根的自动剔除（记 n_drop_tail）

纯读缓存与行情，无副作用；产出 reports/vr_bucket_3buy_20261111.json。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data", "ashare")
SIG_DIR = os.path.join(BASE, "data", "sig_events")
OUT_FP = os.path.join(BASE, "reports", "vr_bucket_3buy_20261111.json")

WINDOWS = [
    ("20251013_20261012",
     os.path.join(SIG_DIR, "sig_events_market_v3_20251013_20261012.json")),
    ("20260930_20261109",
     os.path.join(SIG_DIR, "sig_events_market_v3_20260930_20261109.json")),
]
BUCKETS = [(0.0, 0.7), (0.7, 1.0), (1.0, 1.5), (1.5, 99.0)]
BUCKET_NAMES = ["<0.7", "0.7-1.0", "1.0-1.5", ">1.5"]


def load_close_matrix():
    """全部 ashare parquet → pct_chg 矩阵 (dates × codes)，返回 (codes, dates, L_cumsum)。"""
    files = [f for f in os.listdir(DATA) if f.endswith(".parquet")]
    frames = {}
    dates_all = set()
    for fn in files:
        code = fn[:-8].replace("_", ".")
        if code.endswith(".BJ"):
            continue
        try:
            df = pd.read_parquet(os.path.join(DATA, fn),
                                 columns=["trade_date", "pct_chg"])
            s = pd.Series(df["pct_chg"].values / 100.0,
                          index=pd.Index(df["trade_date"].astype(str), name="d"))
        except Exception:
            # 缺 pct_chg 的文件（旧格式/指数）：用 close/pre_close 现算，同口径
            df = pd.read_parquet(os.path.join(DATA, fn),
                                 columns=["trade_date", "close", "pre_close"])
            pc = df["close"] / df["pre_close"] - 1.0
            s = pd.Series(pc.values,
                          index=pd.Index(df["trade_date"].astype(str), name="d"))
        frames[code] = s
        dates_all.update(s.index)
    dates = sorted(dates_all)
    mat = np.full((len(dates), len(frames)), np.nan)
    codes = list(frames)
    cidx = {c: i for i, c in enumerate(codes)}
    didx = {d: i for i, d in enumerate(dates)}
    for code, s in frames.items():
        ci = cidx[code]
        for d, v in s.items():
            if d in didx:
                mat[didx[d], ci] = v
    # 对数累积和：fwd_log20[t] = S[t+20] - S[t]
    with np.errstate(invalid="ignore"):
        L = np.log1p(mat)
    # 修正：nan 位置贡献 0（nan_to_num 后无 NaN，cumsum 保形）
    S = np.cumsum(np.nan_to_num(L, nan=0.0), axis=0)
    return codes, dates, mat, S


def fwd20_matrix(mat, S, dates):
    """fwd20[t, c] = exp(S[t+20]-S[t]) - 1；尾部不足 20 根 = NaN。"""
    n = len(dates)
    out = np.full_like(mat, np.nan)
    k = 20
    if n > k:
        Sf = np.vstack([S[k:], np.repeat(S[-1][None, :], k, axis=0)])  # S.shift(-k)，尾部补 S[-1]
        with np.errstate(invalid="ignore", over="ignore"):
            out[: n - k] = np.exp(Sf[: n - k] - S[: n - k]) - 1.0
        # 尾部 k 行不完整 → NaN
        out[n - k:] = np.nan
    # 原始 NaN（当日无该票）位置也置 NaN
    out[np.isnan(mat)] = np.nan
    return out


def bench_series(fwd, valid_mask):
    """每日全市场等权 fwd 均值（只算当日有值的票）。"""
    cnt = valid_mask.sum(axis=1)
    ssum = np.nansum(np.where(valid_mask, fwd, 0.0), axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        b = np.where(cnt > 50, ssum / np.maximum(cnt, 1), np.nan)
    return b, cnt


def bucket_stats(excess_bps):
    n = len(excess_bps)
    if n == 0:
        return {"n": 0}
    a = np.asarray(excess_bps, dtype=float)
    mean = a.mean()
    sd = a.std(ddof=1) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 and n > 1 else None
    return {
        "n": int(n),
        "mean_bp": round(float(mean), 1),
        "median_bp": round(float(np.median(a)), 1),
        "win_rate": round(float((a > 0).mean()), 3),
        "t": round(float(t), 2) if t is not None else None,
    }


def main():
    codes, dates, mat, S = load_close_matrix()
    fwd = fwd20_matrix(mat, S, dates)
    valid = ~np.isnan(fwd)
    bench, n_mkt = bench_series(fwd, valid)
    didx = {d: i for i, d in enumerate(dates)}
    cidx = {c: i for i, c in enumerate(codes)}

    # 事件日市值（只取事件涉及的票，避免全量读 daily_basic）
    import datamart as dm
    ev_rows = []
    for wname, fp in WINDOWS:
        d = json.load(open(fp, encoding="utf-8"))
        for e in d["events"]:
            if e["type"] != "3买":
                continue
            ev_rows.append({"win": wname, "stock": e["stock"], "day": str(e["day"]),
                            "vr": e.get("vr"), "score": e.get("score")})
    need_codes = sorted({r["stock"] for r in ev_rows})
    print("3买 事件 %d 条，涉及 %d 只票" % (len(ev_rows), len(need_codes)))

    cap_map = {}
    try:
        for chunk_start in range(0, len(need_codes), 400):
            part = need_codes[chunk_start:chunk_start + 400]
            db = dm.read("daily_basic", start="20251001", end="20261111",
                         codes=part, fields=["ts_code", "trade_date", "circ_mv"])
            if db is not None and len(db):
                for _, r in db.iterrows():
                    cap_map[(str(r["ts_code"]), str(r["trade_date"]))] = float(r["circ_mv"])
        print("circ_mv 覆盖：", len(cap_map), "/", len(ev_rows))
    except Exception as ex:
        print("[warn] daily_basic 读取失败，市值分层跳过：%r" % ex)

    for r in ev_rows:
        r["cap"] = cap_map.get((r["stock"], r["day"]))

    report = {"date": "2026-11-11", "task": "T1 三买×缩量 vr 分桶",
              "bucket_edges": [b for b in BUCKET_NAMES],
              "windows": {}}

    for wname in [w[0] for w in WINDOWS]:
        rows = [r for r in ev_rows if r["win"] == wname]
        n_vr_none = n_tail = 0
        bstats = {k: [] for k in BUCKET_NAMES}
        recs = []
        for r in rows:
            di = didx.get(r["day"])
            ci = cidx.get(r["stock"])
            if di is None or ci is None:
                continue
            f = fwd[di, ci]
            if np.isnan(f):
                n_tail += 1
                continue
            b = bench[di]
            if np.isnan(b):
                continue
            ex_bp = (f - b) * 1e4
            vr = r["vr"]
            if vr is None:
                n_vr_none += 1
                bname = "none"
                bstats.setdefault("none", []).append(ex_bp)
            else:
                bname = None
                for (lo, hi), nm in zip(BUCKETS, BUCKET_NAMES):
                    if lo <= vr < hi:
                        bname = nm
                        break
                if bname is None:
                    bname = ">1.5"
                bstats[bname].append(ex_bp)
            recs.append({"ex": ex_bp, "vr": vr, "cap": r["cap"], "bkt": bname})

        win_rep = {
            "n_events": len(rows), "n_used": len(recs),
            "n_tail_dropped": n_tail, "n_vr_none": n_vr_none,
            "bench_n_days_median": int(np.nanmedian(n_mkt)),
            "buckets": {k: bucket_stats(v) for k, v in bstats.items() if k in BUCKET_NAMES},
        }
        # 市值分层复核：事件 circ_mv 中位数切半
        caps = [r["cap"] for r in recs if r["cap"]]
        if len(caps) > 200:
            med = float(np.median(caps))
            for tag, sel in (("small", lambda c: c < med), ("big", lambda c: c >= med)):
                sub = {k: [] for k in BUCKET_NAMES}
                for r in recs:
                    if r["cap"] and sel(r["cap"]) and r["bkt"] in sub:
                        sub[r["bkt"]].append(r["ex"])
                win_rep["cap_%s" % tag] = {
                    "median_circ_mv": round(med, 1),
                    "buckets": {k: bucket_stats(v) for k, v in sub.items()},
                }
        # 单调性（Spearman 符号检验的简化：桶均值 spearman 与 vr 分档秩相关）
        names_ok = [k for k in BUCKET_NAMES if bstats.get(k)]
        means = [win_rep["buckets"][k]["mean_bp"] for k in names_ok]
        if len(names_ok) == 4:
            rank_vr = range(4)
            rk_mean = pd.Series(means).rank().tolist()
            mono = bool(rk_mean == sorted(rk_mean) or rk_mean == sorted(rk_mean, reverse=True))
        else:
            mono = None
        win_rep["monotonic_low_to_high"] = mono
        report["windows"][wname] = win_rep

        print("\n=== %s  (n_used=%d, 尾部剔除 %d, vr缺失 %d)" % (
            wname, win_rep["n_used"], n_tail, n_vr_none))
        for k in BUCKET_NAMES:
            print("  vr %-8s %s" % (k, win_rep["buckets"].get(k)))
        print("  单调(低vr→高vr):", mono)
        if "cap_small" in win_rep:
            print("  小市值半  :", {k: v["mean_bp"] for k, v in win_rep["cap_small"]["buckets"].items()})
            print("  大市值半  :", {k: v["mean_bp"] for k, v in win_rep["cap_big"]["buckets"].items()})

    os.makedirs(os.path.dirname(OUT_FP), exist_ok=True)
    json.dump(report, open(OUT_FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved:", OUT_FP)


if __name__ == "__main__":
    main()
