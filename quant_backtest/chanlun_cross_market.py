# -*- coding: utf-8 -*-
"""缠论结构特征的跨市场横截面 rank IC 检验（A 股 / 美股 / 港股）。

只做一件事：同一个交易日截面上，结构特征与「未来 10 日复权收益的截面
百分位」之间的 Spearman 相关（rank IC），看三地符号是否一致。

为什么不做绝对收益
------------------
US 282 日、HK ~105 日，月数不够做净 alpha / FM 月度 t。横截面 IC 只要求
每日有足够多的票，不受时间深度直接限制（但 IC 序列长度仍受深度限制，HK
只有 30+ 个截面，t 值参考价值有限，报告里如实标注）。

三个口径坑（本脚本的处置）
--------------------------
1) 复权：三地统一「日收益累乘 → 重建复权收盘」，O/H/L 乘同一因子
   factor = adj_close / close。
   · A 股 pct_chg 全覆盖（tushare 的 pct_chg 以除权除息调整后的 pre_close
     为基数），用它即已含除权处理；
   · US pct_chg/pre_close 只有最后几行有值（增量同步），HK 约 36% 缺失，
     只能退化到相邻 close 比值 → 除息未复权，另加「拆股/合股缺口」识别
     （|ret|>25% 且 close 比值贴近 1/k 或 k，k=2..20，容差 2% → 该日 ret
     记 0），避免一根假跳空劈出假笔。
2) 防前视：特征一律用 _struct_feats(bars, t)，该函数内部只跑
   analyze(bars[:t+1])，天然只含 t 日及之前。另做 PIT 自检：
   feats(bars, t) 必须等于 feats(bars[:t+1], t)。
3) 标签去市场beta：未来 10 日收益在当日截面上取百分位 rank，再算相关，
   A 股普涨 / 美股震荡不会污染 IC。

约束：只新增本文件 + data/chanlun_cross_market.json + _xm_run.log。

用法:
    python chanlun_cross_market.py --workers 12 > _xm_run.log 2>&1
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np

DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "chanlun_cross_market.json")

HOLD = 10          # 未来 10 个交易日
MIN_CTX = 60       # t 日之前至少 60 根，结构才有意义
MIN_XSEC = 30      # 截面票数 < 30 的交易日剔除
MAX_BARS = 900     # 单票只用最近 900 根（少数 A 股 8000+ 根，成本不可控）

# 报告/落盘的特征（前 5 个是任务指定，后 2 个是附带）
FEATS = [
    "st_pv_pos", "st_pv_h", "st_n_bis", "st_amp_ratio", "st_gap_bi",
    "st_n_pivots", "st_last_dir",
]

MARKETS = {
    "A": {"dir": "ashare", "n": 600, "min_bars": 120},
    "US": {"dir": "us", "n": 600, "min_bars": 120},
    # HK 全部文件最大 119 根，达不到 120 的清洗线，降为 95 并如实标注
    "HK": {"dir": "hk", "n": 300, "min_bars": 95},
}


# ---------------------------------------------------------------- 数据与复权
def _is_ashare_stock(fname):
    b = os.path.basename(fname)[:-8]
    code, mkt = b.split("_")
    return (mkt == "SH" and code[:2] in ("60", "68")) or (
        mkt == "SZ" and code[:2] in ("00", "30")
    )


def _num(s):
    return __import__("pandas").to_numeric(s, errors="coerce")


def load_adj_bars(path):
    """parquet → 升序 bars（复权 O/H/L/C）+ 复权收盘数组。"""
    import pandas as pd

    df = pd.read_parquet(path)
    need = [c for c in ("trade_date", "open", "high", "low", "close") if c in df.columns]
    if len(need) < 5:
        return None, None
    df = df.sort_values("trade_date").reset_index(drop=True)
    o = _num(df["open"]).astype(float)
    h = _num(df["high"]).astype(float)
    l = _num(df["low"]).astype(float)
    c = _num(df["close"]).astype(float)
    ok = (c > 0) & (o > 0) & (h >= l) & (h > 0) & (l > 0)
    if ok.sum() < 30:
        return None, None
    dt = df["trade_date"].astype(str).str.replace("-", "", regex=False)[ok].tolist()
    o, h, l, c = o[ok].reset_index(drop=True), h[ok].reset_index(drop=True), \
        l[ok].reset_index(drop=True), c[ok].reset_index(drop=True)

    # 收益：pct_chg → pre_close → 相邻 close
    r = pd.Series(np.nan, index=c.index, dtype=float)
    if "pct_chg" in df.columns:
        r = _num(df["pct_chg"])[ok].reset_index(drop=True) / 100.0
    raw_used = r.isna()
    if "pre_close" in df.columns:
        pre = _num(df["pre_close"])[ok].reset_index(drop=True)
        r2 = c / pre.where(pre > 0) - 1.0
        r = r.where(r.notna(), r2)
        raw_used = r.isna()
    r3 = c / c.shift(1) - 1.0
    r = r.where(r.notna(), r3)
    r = r.astype(float)
    r.iloc[0] = 0.0
    r = r.fillna(0.0)

    # 拆股/合股缺口识别（只在退化到 raw close 比值的日子上做）
    ratio = (c / c.shift(1)).values
    rv = r.values.copy()
    for i in range(1, len(rv)):
        if not raw_used.iloc[i]:
            continue
        if abs(rv[i]) < 0.25:
            continue
        for k in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20):
            for cand in (1.0 / k, float(k)):
                if abs(ratio[i] - cand) / cand < 0.02:
                    rv[i] = 0.0
                    break
            if rv[i] == 0.0:
                break

    adj = c.values[0] * np.cumprod(1.0 + rv)
    adj[0] = c.values[0]
    fac = np.where(c.values > 0, adj / c.values, 1.0)
    fac = np.where(np.isfinite(fac), fac, 1.0)

    vol = _num(df["volume"])[ok].reset_index(drop=True).fillna(0).values if "volume" in df else np.zeros(len(c))
    bars = [
        {"dt": dt[i], "o": float(o[i] * fac[i]), "h": float(h[i] * fac[i]),
         "l": float(l[i] * fac[i]), "c": float(adj[i]), "v": float(vol[i])}
        for i in range(len(dt))
    ]
    return bars, adj


# ------------------------------------------------------------------ 单票任务
def _one(job):
    """单票：逐日截断重放，产出 (dt, code, fwd10, feats...) 行。"""
    import chanlun_struct_features as csf

    code, path = job
    try:
        bars, adj = load_adj_bars(path)
    except Exception:
        return []
    if not bars:
        return []
    n = len(bars)
    if n < MIN_CTX + HOLD + 5:
        return []
    out = []
    for t in range(MIN_CTX, n - HOLD):
        # 只喂 bars[:t+1]，结构状态严格 PIT
        f = csf._struct_feats(bars, t)
        fwd = adj[t + HOLD] / adj[t] - 1.0
        if not np.isfinite(fwd):
            continue
        out.append(
            (bars[t]["dt"], code, float(fwd)) + tuple(
                (float(f[k]) if f.get(k) is not None and np.isfinite(f.get(k)) else np.nan)
                for k in FEATS
            )
        )
    return out


def _pit_probe(job):
    """防前视自检：feats(全序列, t) 是否等于 feats(bars[:t+1], t)。"""
    import chanlun_struct_features as csf

    code, path, ts = job
    bars, _ = load_adj_bars(path)
    if not bars:
        return None
    n = len(bars)
    same = tot = 0
    for t in ts:
        if not (MIN_CTX <= t < n - 1):
            continue
        a = csf._struct_feats(bars, t)
        b = csf._struct_feats(bars[: t + 1], t)
        for k in FEATS:
            va, vb = a.get(k), b.get(k)
            tot += 1
            if (va is None and vb is None) or (
                va is not None and vb is not None and abs(float(va) - float(vb)) < 1e-12
            ):
                same += 1
    return same, tot


# ------------------------------------------------------------------ IC 统计
def _spearman(x, y):
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    d = math.sqrt(float((rx * rx).sum()) * float((ry * ry).sum()))
    if d <= 0:
        return None
    return float((rx * ry).sum() / d)


def ic_stats(df, feats):
    res = {}
    days_all = sorted(df["dt"].unique())
    for k in feats:
        ics, ns = [], []
        for d, g in df.groupby("dt", sort=True):
            sub = g[[k, "fwd"]].dropna()
            if len(sub) < MIN_XSEC:
                continue
            v = _spearman(sub[k].values.astype(float), sub["fwd"].values.astype(float))
            if v is None or not np.isfinite(v):
                continue
            ics.append(v)
            ns.append(len(sub))
        if len(ics) < 5:
            res[k] = {"days": len(ics), "ic": None, "t": None,
                      "pos_rate": None, "avg_xsec": (float(np.mean(ns)) if ns else None),
                      "insufficient": True}
            continue
        arr = np.array(ics, dtype=float)
        sd = float(arr.std(ddof=1))
        # 标签重叠 HOLD 日 → 独立样本约 n/HOLD，另给非重叠抽样 t（每 HOLD 日取 1）
        sub = arr[::HOLD]
        sd2 = float(sub.std(ddof=1)) if len(sub) > 2 else 0.0
        res[k] = {
            "days": int(len(arr)),
            "ic": round(float(arr.mean()), 4),
            "t": round(float(arr.mean() / (sd / math.sqrt(len(arr)))), 2) if sd > 0 else None,
            "t_nonoverlap": round(float(sub.mean() / (sd2 / math.sqrt(len(sub)))), 2)
            if sd2 > 0 else None,
            "n_nonoverlap": int(len(sub)),
            "pos_rate": round(float((arr > 0).mean()) * 100, 1),
            "avg_xsec": round(float(np.mean(ns)), 1),
            "ic_std": round(sd, 4),
        }
    return res, len(days_all)


def main():
    ap = argparse.ArgumentParser(description="缠论结构特征跨市场 rank IC")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    t0 = time.time()
    picks = {}
    meta = {}
    for mk, cfg in MARKETS.items():
        fs = sorted(glob.glob(os.path.join(DATA, cfg["dir"], "*.parquet")))
        if mk == "A":
            fs = [f for f in fs if _is_ashare_stock(f)]
        import pyarrow.parquet as pq
        cand = []
        for f in fs:
            try:
                if pq.ParquetFile(f).metadata.num_rows >= cfg["min_bars"]:
                    cand.append(f)
            except Exception:
                continue
        rnd = random.Random(42)
        take = min(cfg["n"], len(cand))
        picks[mk] = sorted(rnd.sample(cand, take))
        meta[mk] = {"dir": cfg["dir"], "candidates": len(cand), "sampled": take,
                    "min_bars": cfg["min_bars"]}
        print("[%s] 候选 %d / 抽样 %d（min_bars=%d）" % (mk, len(cand), take, cfg["min_bars"]))

    # PIT 自检样本（每地 6 只 × 8 个随机 t）
    probe_jobs = []
    for mk in MARKETS:
        rnd = random.Random(7)
        for f in rnd.sample(picks[mk], min(6, len(picks[mk]))):
            probe_jobs.append((os.path.basename(f)[:-8], f,
                               [rnd.randint(MIN_CTX, 200) for _ in range(8)]))
    pit = {"same": 0, "total": 0}
    with ProcessPoolExecutor(max_workers=min(a.workers, len(probe_jobs))) as ex:
        for r in ex.map(_pit_probe, probe_jobs):
            if r:
                pit["same"] += r[0]
                pit["total"] += r[1]
    print("PIT 自检：%d/%d 个特征点完全一致（%.2f%%）用时 %.0fs"
          % (pit["same"], pit["total"], 100.0 * pit["same"] / max(1, pit["total"]), time.time() - t0))

    jobs = [(os.path.basename(f)[:-8], f) for mk in MARKETS for f in picks[mk]]
    mk_of = {os.path.basename(f)[:-8]: mk for mk in MARKETS for f in picks[mk]}

    import pandas as pd
    rows = []
    done = 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(_one, jobs, chunksize=2):
            rows.extend(part)
            done += 1
            if done % 200 == 0:
                print("  ... %d/%d 只，%d 行，%.0fs" % (done, len(jobs), len(rows), time.time() - t0))
    print("逐票特征完成：%d 行，用时 %.0fs" % (len(rows), time.time() - t0))
    if not rows:
        print("无样本")
        return

    cols = ["dt", "code", "fwd"] + FEATS
    df = pd.DataFrame(rows, columns=cols)
    df["mk"] = df["code"].map(mk_of)
    print("日期范围 " + " / ".join(
        "%s %s~%s" % (mk, df[df.mk == mk]["dt"].min(), df[df.mk == mk]["dt"].max())
        for mk in MARKETS if (df.mk == mk).any()))

    out = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "caliber": {
               "adj": "pct_chg 累乘重建复权收盘、O/H/L 同因子；A 股 pct_chg 全覆盖（含除权除息）；"
                      "US/HK pct_chg 大面积缺失→退化相邻 close 比值（除息未复权），"
                      "另加拆股缺口识别（|ret|>25% 且比值≈1/k 或 k → ret=0）",
               "label": "未来 %d 交易日复权收益，按交易日截面取 pct rank" % HOLD,
               "ic": "每日截面 Spearman(特征, 收益rank)，截面票数>=%d 才计入" % MIN_XSEC,
               "pit": "逐日截断重放 analyze(bars[:t+1])，非全历史回填",
               "hold_days": HOLD, "min_ctx": MIN_CTX, "min_xsec": MIN_XSEC,
           },
           "pit_check": {"same": pit["same"], "total": pit["total"],
                         "pass_rate": round(100.0 * pit["same"] / max(1, pit["total"]), 2)},
           "markets": {}}

    for mk in MARKETS:
        sub = df[df.mk == mk]
        if sub.empty:
            out["markets"][mk] = {"insufficient": True}
            continue
        st, ndays = ic_stats(sub, FEATS)
        xs = sub.groupby("dt").size()
        out["markets"][mk] = {
            "n_stocks": int(sub["code"].nunique()),
            "n_rows": int(len(sub)),
            "n_days_total": int(ndays),
            "date_from": str(sub["dt"].min()), "date_to": str(sub["dt"].max()),
            "avg_xsec_all": round(float(xs.mean()), 1),
            "feature_ic": st,
        }

    # 同窗口对照：A 股切到 US 的日期区间，剔除 regime/时段差异后仍异号才算真异号
    us = df[df.mk == "US"]
    if not us.empty:
        lo, hi = us["dt"].min(), us["dt"].max()
        a_iso = df[(df.mk == "A") & (df.dt >= lo) & (df.dt <= hi)]
        if len(a_iso):
            st_iso, _ = ic_stats(a_iso, FEATS)
            out["markets"]["A"]["feature_ic_iso_us_window"] = st_iso
            out["markets"]["A"]["iso_window"] = [str(lo), str(hi)]
            out["markets"]["A"]["iso_rows"] = int(len(a_iso))

    # 三地同号判定
    cmp_ = {}
    for k in FEATS:
        row = {}
        for mk in MARKETS:
            m = out["markets"].get(mk) or {}
            fi = (m.get("feature_ic") or {}).get(k) or {}
            row[mk] = {"ic": fi.get("ic"), "t": fi.get("t")}
        a_iso = ((out["markets"].get("A") or {}).get("feature_ic_iso_us_window") or {}).get(k) or {}
        row["A_iso"] = {"ic": a_iso.get("ic"), "t": a_iso.get("t")}
        ics = {mk: row[mk]["ic"] for mk in MARKETS if row[mk]["ic"] is not None}
        sign_ok = (len(ics) >= 2 and len({1 if v > 0 else -1 for v in ics.values()}) == 1)
        magnitudes = {mk: abs(v) for mk, v in ics.items()}
        ratio = None
        if "A" in magnitudes and "US" in magnitudes and min(magnitudes["A"], magnitudes["US"]) > 0:
            ratio = round(max(magnitudes["A"], magnitudes["US"]) /
                          min(magnitudes["A"], magnitudes["US"]), 2)
        row["same_sign"] = bool(sign_ok) and len(ics) == len(MARKETS)
        row["A_vs_US_abs_ratio"] = ratio
        # 同窗口下 A 与 US 是否同号
        if row["A_iso"]["ic"] is not None and row["US"]["ic"] is not None:
            row["same_sign_Aiso_US"] = (row["A_iso"]["ic"] > 0) == (row["US"]["ic"] > 0)
        cmp_[k] = row
    out["cross_market"] = cmp_

    # 与既有 A 股实验对账
    prior_p = os.path.join(DATA, "chanlun_struct_rank.json")
    if os.path.exists(prior_p):
        prior = json.load(open(prior_p, encoding="utf-8"))
        out["vs_prior_ashare"] = {
            "file": "data/chanlun_struct_rank.json",
            "prior_test_ic_struct_only": prior.get("ic", {}).get("仅结构", {}).get("test"),
            "prior_test_ic_all": prior.get("ic", {}).get("全量", {}).get("test"),
            "note": "前期为信号池内 36 特征复合 rank 的 IC（315 日测试段），本脚本为单特征逐日全截面 IC，"
                    "口径不同，只比量级与符号层次",
        }

    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("-> %s  总用时 %.0fs" % (a.out, time.time() - t0))

    # 控制台对比表
    print("\n%-13s %-16s %-16s %-16s %-16s %s" % (
        "特征", "A股全段 IC(t)", "A股同窗口 IC(t)", "US IC(t)", "HK IC(t)", "三地同号/A同窗同号"))
    for k in FEATS:
        cells = []
        fi_all = out["markets"]["A"]["feature_ic"].get(k) or {}
        fi_iso = (out["markets"]["A"].get("feature_ic_iso_us_window") or {}).get(k) or {}
        seq = [fi_all, fi_iso] + [(out["markets"].get(mk, {}).get("feature_ic") or {}).get(k) or {}
                                  for mk in ("US", "HK")]
        for fi in seq:
            cells.append("%8s(%6s)" % (
                "%.4f" % fi["ic"] if fi.get("ic") is not None else "NA",
                "%.2f" % fi["t"] if fi.get("t") is not None else "NA"))
        print("%-13s %s %s" % (k, " ".join(cells),
                               "%s/%s" % ("Y" if cmp_[k]["same_sign"] else "N",
                                          "Y" if cmp_[k].get("same_sign_Aiso_US") else "N")))


if __name__ == "__main__":
    main()
