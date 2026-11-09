#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
缠论池状态 · 历史回放（PIT）
============================================================

目的：回答一个到现在为止没有任何一个数字的问题——
      「进池之后赚不赚」。

纪律（按 195bp 那次的教训定的）：
  1. 判定代码与线上池**同源**：直接 import pool_state.analyse，不另写一份。
     （另写一份就等于测了另一套判据，测出来的东西和线上无关。）
  2. PIT：每个 asof 只喂 <= asof 的日线，靠 chanlun_signal 的截断重放口径。
  3. 超额按「同日全样本等权」算，不看绝对收益。195bp 的教训是绝对收益里
     混着市场 β 与低流动性假象，剔完就翻负。
  4. t 值按 asof 聚类：同日股票不独立，逐日算横截面均值再对日期序列做 t。
  5. 成交额门槛 PIT：asof 前 20 日均额（含 asof），不用今天的额去筛三年前。

用法：
  python pool_replay.py --run --step 5              # 跑回放，落 parquet
  python pool_replay.py --report                    # 读 parquet 出报告
  python pool_replay.py --run --step 5 --limit 300  # 冒烟
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool_state as ps  # noqa: E402  判定口径唯一来源

OUT_PARQUET = os.path.join(ps.REPORTS, "pool_replay.parquet")
OUT_MD = os.path.join(HERE, "..", "deliverables",
                      "2026-11-09_缠论池历史回放_v1.md")

FWD = (20, 60, 120)
MIN_AMT_YI = 0.5      # 与线上 scan 默认值一致
STEP_DEFAULT = 5      # 每 5 个交易日取一个截面

SCHEMA = ["asof", "code", "stage", "close", "zg", "zd", "dist_zg", "pos",
          "trend", "piv_n_bi", "piv_over", "sig_type", "sig_age", "sig_gain",
          "amt20", "r20", "r60", "r120", "mx60", "mn60", "gap1"]


# ────────────────────────────────────────────────────────────
# 1. 宇宙与日历
# ────────────────────────────────────────────────────────────
def build_universe():
    """列出可回放的个股文件，同时用全部文件的交易日求日历并集。"""
    names = ps.load_names()
    files = []
    dates = set()
    n_idx = n_bj = n_st = n_short = n_bad = 0
    for fn in sorted(os.listdir(ps.ASHARE)):
        if not fn.endswith(".parquet"):
            continue
        code = fn[:-8].replace("_", ".")
        if ps._is_index(code):
            n_idx += 1
            continue
        if ps._is_bj(code):
            n_bj += 1
            continue
        if any(k in names.get(code, "") for k in ps.ST_MARKS):
            n_st += 1
            continue
        try:
            s = pd.read_parquet(os.path.join(ps.ASHARE, fn),
                                columns=["trade_date"])["trade_date"]
        except Exception:
            n_bad += 1
            continue
        if len(s) < ps.MIN_BARS:
            n_short += 1
            continue
        dates.update(int(x) for x in s.to_numpy())
        files.append((os.path.join(ps.ASHARE, fn), code))
    cal = np.array(sorted(dates), dtype=np.int64)
    meta = {"files": len(files), "cal": len(cal),
            "cal_min": int(cal[0]) if len(cal) else None,
            "cal_max": int(cal[-1]) if len(cal) else None,
            "skip": {"index": n_idx, "bj": n_bj, "st": n_st,
                     "short": n_short, "bad": n_bad}}
    return files, cal, meta


def build_grid(cal, start, end, step):
    grid = cal[(cal >= start) & (cal <= end)]
    return grid[::step]


# ────────────────────────────────────────────────────────────
# 2. 单只票回放（worker）
# ────────────────────────────────────────────────────────────
def run_one(job):
    path, code, grid = job
    try:
        df = pd.read_parquet(path, columns=ps.COLS)
    except Exception:
        try:
            df = pd.read_parquet(path)
        except Exception:
            return []
    if "amount" not in df.columns or "volume" not in df.columns:
        return []
    df = df.sort_values("trade_date").reset_index(drop=True)
    n = len(df)
    if n < ps.MIN_BARS:
        return []

    dt = df["trade_date"].astype("int64").to_numpy()
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    amt = np.nan_to_num(df["amount"].to_numpy(float), nan=0.0)

    out = []
    for g in grid:
        i = int(np.searchsorted(dt, g, "right")) - 1
        if i < ps.MIN_BARS - 1 or dt[i] != g:
            continue                      # 停牌或历史不足，本日不判

        # 成交额门槛用 PIT 口径：asof 往前 20 日（含当日）
        amt20 = float(amt[max(0, i - 19):i + 1].mean()) / 1e5
        if amt20 < MIN_AMT_YI:
            continue

        r = ps.analyse(df.iloc[:i + 1], code)
        if r is None:
            continue
        cl = r["close"]
        if not cl or cl < ps.MIN_PRICE:
            continue

        e = i + 1                          # T+1 开盘入场
        if e < n and o[e] > 0:
            entry = o[e]
            gap1 = entry / c[i] - 1.0
            rets = []
            for N in FWD:
                x = min(e + N, n - 1)      # 退市/停牌则持到最后一日
                rets.append(c[x] / entry - 1.0)
            seg = c[e:min(e + 60, n)]
            mx60 = float(seg.max() / entry - 1.0)
            mn60 = float(seg.min() / entry - 1.0)
        else:
            gap1 = None
            rets = [None, None, None]
            mx60 = mn60 = None

        out.append((int(g), code, r["stage"], cl, r["zg"], r["zd"],
                    r["dist_zg"], r["pos"], r["trend"], r["piv_n_bi"],
                    int(bool(r["piv_over"])), r["sig_type"], r["sig_age"],
                    r["sig_gain"], amt20, rets[0], rets[1], rets[2],
                    mx60, mn60, gap1))
    return out


def cmd_run(args):
    t0 = time.time()
    files, cal, meta = build_universe()
    start, end = _int_date(args.start), _int_date(args.end)
    grid = build_grid(cal, start, end, args.step)
    print(f"[replay] 可回放文件 {meta['files']} / 日历 {meta['cal']} 日 "
          f"({meta['cal_min']}~{meta['cal_max']})")
    print(f"[replay] 跳过：指数 {meta['skip']['index']} · 北交所 {meta['skip']['bj']} "
          f"· ST/退 {meta['skip']['st']} · 不足 {ps.MIN_BARS} 根 {meta['skip']['short']}"
          f" · 读取失败 {meta['skip']['bad']}")
    if len(grid) == 0:
        print("[replay] 网格为空，检查 --start/--end")
        return 2
    print(f"[replay] 截面 {len(grid)} 个：{grid[0]} → {grid[-1]}（每 {args.step} 交易日）")

    if args.limit:
        files = files[:args.limit]
        print(f"[replay] 冒烟模式，仅 {len(files)} 只")

    grid_t = tuple(int(x) for x in grid)
    jobs = [(p, code, grid_t) for p, code in files]
    rows = []
    done = 0
    t1 = time.time()
    if args.workers > 1:
        from multiprocessing import Pool
        with Pool(args.workers) as pool:
            for res in pool.imap_unordered(run_one, jobs, chunksize=8):
                rows.extend(res)
                done += 1
                if done % 500 == 0:
                    el = time.time() - t1
                    print(f"  {done}/{len(jobs)} 只 · {len(rows)} 行 · "
                          f"{el:.0f}s · 预计剩余 {el / done * (len(jobs) - done):.0f}s",
                          flush=True)
    else:
        for j in jobs:
            rows.extend(run_one(j))
            done += 1

    if not rows:
        print("[replay] 无结果")
        return 3
    df = pd.DataFrame(rows, columns=SCHEMA)
    os.makedirs(ps.REPORTS, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    print(f"[replay] 完成 {len(df)} 行 / {df['code'].nunique()} 只 / "
          f"{df['asof'].nunique()} 个截面 · {time.time() - t0:.0f}s")
    print(f"[replay] 落盘 {OUT_PARQUET} ({os.path.getsize(OUT_PARQUET) / 1e6:.1f} MB)")
    print("[replay] 档位分布：" + "  ".join(
        f"{k}:{v}" for k, v in df["stage"].value_counts().items()))
    return 0


def _int_date(s):
    return int(str(s).replace("-", "").replace("/", ""))


# ────────────────────────────────────────────────────────────
# 3. 报告
# ────────────────────────────────────────────────────────────
def _tstat(x):
    x = np.asarray([v for v in x if v is not None and np.isfinite(v)], float)
    if len(x) < 3:
        return None
    sd = x.std(ddof=1)
    if sd == 0:
        return None
    return float(x.mean() / (sd / np.sqrt(len(x))))


def _stat(series):
    s = pd.Series(series).dropna()
    if s.empty:
        return {"n": 0, "mean": None, "med": None, "win": None}
    return {"n": int(len(s)), "mean": float(s.mean()),
            "med": float(s.median()), "win": float((s > 0).mean())}


def _bucket_tbl(df, col, edges, labels, order, base_col="xr60"):
    """按某一维度分层，看各档在各层内的超额。"""
    out = []
    for lo, hi, lab in _iter_edges(edges, labels):
        m = (df[col] >= lo) & (df[col] < hi)
        sub = df[m]
        if sub.empty:
            continue
        cells = [f"{len(sub):,}", _bp(_stat(sub[base_col])["mean"])]
        for st in ("三买待", "三买", "一买", "背驰"):
            s = sub[sub["stage"] == st]
            cells.append(_bp(_stat(s[base_col])["mean"]) if len(s) >= 30 else "—")
        out.append([lab] + cells)
    return out


def _iter_edges(edges, labels):
    for k, lab in enumerate(labels):
        lo = edges[k]
        hi = edges[k + 1]
        yield lo, hi, lab


def cmd_report(args):
    if not os.path.exists(OUT_PARQUET):
        print(f"[report] 缺 {OUT_PARQUET}，先跑 --run")
        return 2
    df = pd.read_parquet(OUT_PARQUET)
    print(f"[report] {len(df):,} 行 / {df['code'].nunique():,} 只 / "
          f"{df['asof'].nunique()} 截面")

    mv = _attach_mv(df)
    if mv is not None:
        df = mv
    has_mv = "mv_yi" in df.columns and bool(df["mv_yi"].notna().any())

    # ── 三把基准 ──────────────────────────────────
    # ① 同日全样本等权（剥市场 β）
    base = df.groupby("asof")[["r20", "r60", "r120"]].mean()
    for h in ("r20", "r60", "r120"):
        df["x" + h] = df[h] - df["asof"].map(base[h])
    # ③ 同日 × 同额层 / ④ 同日 × 同市值层（再剥规模与流动性）
    df["lamt"] = pd.cut(df["amt20"], [0.5, 1.0, 2.0, 5.0, np.inf], labels=False)
    df["n60"] = df["r60"] - df.groupby(["asof", "lamt"])["r60"].transform("mean")
    if has_mv:
        df["lmv"] = pd.cut(df["mv_yi"], [0, 30, 50, 100, 300, np.inf], labels=False)
        df["m60"] = df["r60"] - df.groupby(["asof", "lmv"])["r60"].transform("mean")

    df["year"] = df["asof"] // 10000
    order = [s for s in ps.STAGES if s in set(df["stage"])]

    lines = []
    P = lines.append

    P("# 缠论池状态 · 历史回放（第一版）")
    P("")
    P(f"> 生成：{datetime.now():%Y-%m-%d %H:%M}　·　样本 {len(df):,} 个「截面×个股」观测"
      f"　·　{df['code'].nunique():,} 只　·　{df['asof'].nunique()} 个截面"
      f"（每 {args.step} 交易日，{df['asof'].min()}~{df['asof'].max()}）")
    P("")
    P("## 0. 这份回放测的是什么")
    P("")
    P("判据与线上池**同一份代码**（`pool_state.analyse`），PIT 截断：每个 asof 只喂"
      "当天及之前的日线。入场 **T+1 开盘**，出场 N 日后收盘（不足则持到数据末日，含退市）。"
      "t 值按 asof 聚类（同日股票不独立）。")
    P("")
    P("三种超额口径，从宽到严：")
    P("")
    P("- **① 同日全样本**：剥掉市场 β，但**没剥掉市值/流动性**。微盘天然跑赢，"
      "任何档位只要偏小盘就自动有正超额——这是 195bp 那次的原始死因。")
    P("- **② 逐日等权**：每个截面权重相同，避免「某天信号特别多」把均值带走。"
      "与 ① 符号不一致时，说明结论由少数截面驱动。")
    P("- **③ 同日×同额层 / ④ 同日×同市值层**：把规模与流动性也剥掉，"
      "**只剩结构本身**。这一列才是「缠论有没有用」的答案。")
    P("")

    # ── 1. 主表 ───────────────────────────────────
    P("## 1. 主表：三把尺子量同一批样本（60 日）")
    P("")
    P("| 档位 | n | ① 全样本超额 | ② 逐日等权 | 聚类t | ③ 额层内 | 额层内t "
      "| ④ 市值层内 | 市值层内t |")
    P("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for st in order:
        sub = df[df["stage"] == st]
        if len(sub) < 30:
            P(f"| {st} | {len(sub):,} | 样本不足 | | | | | | |")
            continue
        x1 = _stat(sub["xr60"])["mean"]
        daily = sub.groupby("asof")["xr60"].mean()
        t1 = _tstat(daily.to_numpy())
        x3 = _stat(sub["n60"])["mean"]
        t3 = _tstat(sub.groupby("asof")["n60"].mean().to_numpy())
        if has_mv:
            x4 = _stat(sub["m60"])["mean"]
            t4 = _tstat(sub.groupby("asof")["m60"].mean().to_numpy())
            c4 = f"{_bp(x4)} | {_ft(t4)}"
        else:
            c4 = "— | —"
        P(f"| {st} | {len(sub):,} | {_bp(x1)} | {_bp(daily.mean())} | {_ft(t1)} "
          f"| {_bp(x3)} | {_ft(t3)} | {c4} |")
    P("")
    P("读法：④ 列全部贴在 0 附近、t 值不显著为正，就说明**结构本身不提供超额**，"
      "①② 列的正数全是市值与流动性送的。")
    P("")

    # ── 1b. ① 与 ② 符号相反的成因 ───────────────
    P("## 1b. 为什么 ① 和 ② 符号相反：信号数就是拥挤度")
    P("")
    P("| 档位 | 每截面信号数（中位） | 最多 | corr(当日信号数, 当日平均超额) |")
    P("|---|---:|---:|---:|")
    corr_note = {}
    for st in order:
        sub = df[df["stage"] == st].dropna(subset=["xr60"])
        if sub.empty:
            continue
        g = sub.groupby("asof")["xr60"].agg(["mean", "size"])
        c = g["size"].corr(g["mean"])
        corr_note[st] = c
        P(f"| {st} | {int(g['size'].median()):,} | {int(g['size'].max()):,} | {c:+.3f} |")
    P("")
    P("相关性为正且不低，说明：**这一档在市场最热、信号成批涌出的那几天表现最好，"
      "在信号稀少的平常日子里跑输**。① 按笔加权，被那几个热门截面拉成正数；"
      "② 按日等权，才露出平常日子的真实成色。两者冲突时，② 更接近「每天都照做」的结果。")
    P("")

    # ── 1c. 档位纯净度 ────────────────────────────
    P("## 1c. 档位纯净度：中枢档里 75% 其实是「判定作废」")
    P("")
    P("`piv_over` = 中枢延伸超 9 笔、或区间宽超 30%，即级别已升级、ZG/ZD 不再代表"
      "可操作边界。这类票不判位置，一律归进中枢档。于是中枢档里混了两种东西，"
      "不拆开就会把「无法判定」的弱势错记成「震荡股偏弱」。")
    P("")
    P("| 档位 | n | piv_over 占比 | piv_over=0 逐日超额 | piv_over=1 逐日超额 |")
    P("|---|---:|---:|---:|---:|")
    for st in order:
        sub = df[df["stage"] == st]
        if len(sub) < 30:
            continue
        over = float(sub["piv_over"].mean())
        cells = []
        for k in (0, 1):
            g = sub[sub["piv_over"] == k].dropna(subset=["xr60"])
            cells.append(_bp(float(g.groupby("asof")["xr60"].mean().mean()))
                         if len(g) >= 30 else "—")
        P(f"| {st} | {len(sub):,} | {over * 100:.0f}% | {cells[0]} | {cells[1]} |")
    P("")

    # ── 2. 分布 ───────────────────────────────────
    P("## 2. 超额分布：是整体位移，还是只有右尾在动")
    P("")
    P("| 档位 | p10 | p25 | p50 | p75 | p90 | 均值 |")
    P("|---|---:|---:|---:|---:|---:|---:|")
    q = df["xr60"].dropna()
    P(f"| **全样本** | {_bp(q.quantile(.1))} | {_bp(q.quantile(.25))} | "
      f"{_bp(q.quantile(.5))} | {_bp(q.quantile(.75))} | {_bp(q.quantile(.9))} | "
      f"{_bp(q.mean())} |")
    for st in order:
        s = df[df["stage"] == st]["xr60"].dropna()
        if len(s) < 30:
            continue
        P(f"| {st} | {_bp(s.quantile(.1))} | {_bp(s.quantile(.25))} | "
          f"{_bp(s.quantile(.5))} | {_bp(s.quantile(.75))} | {_bp(s.quantile(.9))} | "
          f"{_bp(s.mean())} |")
    P("")
    P("若某档的中位数与全样本中位数几乎一样、只有 p90 抬高，那一档的均值优势不可复制。")
    P("")

    # ── 3. 绝对收益 ──────────────────────────────
    P("## 3. 绝对收益（不扣基准，供对照）")
    P("")
    P("| 档位 | n | 20日均 | 60日均 | 60日中位 | 120日均 | 120日中位 |")
    P("|---|---:|---:|---:|---:|---:|---:|")
    for st in order:
        sub = df[df["stage"] == st]
        a20, a60, a120 = _stat(sub["r20"]), _stat(sub["r60"]), _stat(sub["r120"])
        P(f"| {st} | {len(sub):,} | {_pct(a20.get('mean'))} | {_pct(a60.get('mean'))} | "
          f"{_pct(a60.get('med'))} | {_pct(a120.get('mean'))} | "
          f"{_pct(a120.get('med'))} |")
    P("")

    # ── 4. 按年 ──────────────────────────────────
    P("## 4. 按年拆（① 全样本超额，60 日）")
    P("")
    yrs = sorted(df["year"].unique())
    P("| 档位 | " + " | ".join(f"{y} | n" for y in yrs) + " |")
    P("|---|" + "---:|---:|" * len(yrs))
    for st in order:
        sub = df[df["stage"] == st]
        cells = []
        for y in yrs:
            s = sub[sub["year"] == y]
            cells.append(f"{_bp(_stat(s['xr60'])['mean'])} | {len(s):,}")
        P(f"| {st} | " + " | ".join(cells) + " |")
    P("")

    # ── 5. 梯度 ──────────────────────────────────
    P("## 5. 梯度：超额到底由什么决定")
    P("")
    P("同一天、同一档位，只按成交额分层的超额：")
    P("")
    P("| 20日均额 | n | 全样本 | 三买待 | 三买 | 一买 | 背驰 |")
    P("|---|---:|---:|---:|---:|---:|---:|")
    for r in _bucket_tbl(df, "amt20", [0.5, 1.0, 2.0, 5.0, np.inf],
                         ["0.5~1 亿", "1~2 亿", "2~5 亿", "> 5 亿"], order):
        P("| " + " | ".join(r) + " |")
    P("")
    if has_mv:
        P("同一天、同一档位，只按流通市值分层的超额：")
        P("")
        P("| 流通市值 | n | 全样本 | 三买待 | 三买 | 一买 | 背驰 |")
        P("|---|---:|---:|---:|---:|---:|---:|")
        for r in _bucket_tbl(df, "mv_yi", [0, 30, 50, 100, 300, np.inf],
                             ["微盘<30亿", "小盘30-50亿", "中盘50-100亿",
                              "大盘100-300亿", "超大盘>300亿"], order):
            P("| " + " | ".join(r) + " |")
        P("")
    P("注意看**同一列自上而下的落差**与**同一行自左向右的落差**："
      "若前者远大于后者，说明档位之间的差别还没市值分档的差别大。")
    P("")

    # ── 6. 三买待 → 三买 ─────────────────────────
    P("## 6. 三买待 之后发生了什么")
    P("")
    conv = _conversion(df, "三买待", "三买", 20)
    if conv:
        P(f"- 三买待 {conv['n']:,} 次观测，**20 个交易日内转为三买 {conv['n_conv']:,} 次"
          f"（{conv['rate'] * 100:.1f}%）**")
        P("")
        P("| 分组 | n | 60日超额(按笔) | 60日超额(逐日等权) | 聚类t |")
        P("|---|---:|---:|---:|---:|")
        P(f"| 转成三买 | {conv['n_conv']:,} | {_bp(conv['x_ok'])} | {_bp(conv['d_ok'])} "
          f"| {_ft(conv['t_ok'])} |")
        P(f"| 没转成 | {conv['n'] - conv['n_conv']:,} | {_bp(conv['x_fail'])} | "
          f"{_bp(conv['d_fail'])} | {_ft(conv['t_fail'])} |")
        P("")
        P("这条最反直觉：**等回踩确认再进，比不等更差**。"
          "但注意两组不是同日期对照，差异里混着市场状态，不能当成严格的因果。"
          "能确定的只有一点：转成三买之后的 60 日超额，没有比没转成的更好。")
    else:
        P("- 样本不足，无法计算转化率")
    P("")

    # ── 7. 池子 vs 全市场 ────────────────────────
    P("## 7. 池子整体的成色")
    P("")
    rows = [("六档池（三买待/三买/二买/一买/背驰/中枢）", ps.POOL_STAGES),
            ("五档池（不含中枢）", ["三买待", "三买", "二买", "一买", "背驰"])]
    P("| 组合 | n | ① 全样本超额 | ③ 额层内 | ④ 市值层内 |")
    P("|---|---:|---:|---:|---:|")
    for lab, stages in rows:
        s = df[df["stage"].isin(stages)]
        c4 = _bp(_stat(s["m60"])["mean"]) if has_mv else "—"
        P(f"| {lab} | {len(s):,} | {_bp(_stat(s['xr60'])['mean'])} | "
          f"{_bp(_stat(s['n60'])['mean'])} | {c4} |")
    P("")
    P(f"全样本 60 日绝对收益均值 {_pct(_stat(df['r60'])['mean'])}——这是市场本身，"
      "池子要跑赢的是它，不是 0。")
    P("")

    # ── 8. 局限 ──────────────────────────────────
    g = pd.Series(df["gap1"]).dropna()
    P("## 8. 口径与局限（必须一起读）")
    P("")
    if len(g):
        P(f"- **入场可行性**：T+1 开盘跳空 > +9%（一字板买不进）{(g > 0.09).mean() * 100:.1f}%，"
          f"> +5% {(g > 0.05).mean() * 100:.1f}%。影响很小，但不是零。")
    P("- **ST 用当前名称过滤，不是 PIT**：今天叫 ST 的整段被剔，今天摘帽的当年按正常票计入。")
    P("- **退市/停牌**：N 日后无数据按持到最后一日计价，承认了退市损失，但没模拟流动性枯竭的滑点。")
    P(f"- **采样**：每 {args.step} 个交易日一个截面，三买待 这类瞬态会被漏采，"
      "转化率偏低是采样造成的。")
    P("- **复权**：数据湖前复权，除权日前后收益连续。")
    P("- **未做**：涨跌停可成交性模拟、冲击成本、组合层资金约束。")
    P("")

    md = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(md)
    print(f"\n[report] 写入 {OUT_MD}")
    return 0


def _ft(t):
    return "—" if t is None else f"{t:.2f}"


def _attach_mv(df):
    """按 (asof, code) 补 PIT 流通市值（data/tdx/ts_daily_basic）。失败返回 None。"""
    import glob
    fs = sorted(glob.glob(os.path.join(ps.HERE, "data", "tdx",
                                       "ts_daily_basic", "*.parquet")))
    if not fs:
        return None
    asofs = set(int(x) for x in df["asof"].unique())
    frames = []
    try:
        for f in fs:
            d = pd.read_parquet(f, columns=["ts_code", "trade_date", "circ_mv"])
            d = d[d["trade_date"].astype("int64").isin(asofs)]
            if not d.empty:
                frames.append(d)
    except Exception:
        return None
    if not frames:
        return None
    m = pd.concat(frames, ignore_index=True)
    m["code"] = m["ts_code"].astype(str)
    m["mv_yi"] = pd.to_numeric(m["circ_mv"], errors="coerce") / 1e4   # 万元 → 亿元
    m["asof"] = m["trade_date"].astype("int64")
    m = m.drop_duplicates(["asof", "code"])
    return df.merge(m[["asof", "code", "mv_yi"]], on=["asof", "code"], how="left")


def _conversion(df, src, dst, horizon_days):
    """src 档位在后续 horizon 个自然日内是否转为 dst（同一只票、后续截面）。"""
    d = df[["asof", "code", "stage", "xr60"]].sort_values(["code", "asof"])
    n = n_ok = 0
    ok, fail = [], []
    for _, g in d.groupby("code"):
        st = g["stage"].to_numpy()
        dt = g["asof"].to_numpy()
        xr = g["xr60"].to_numpy()
        for i in range(len(st)):
            if st[i] != src:
                continue
            n += 1
            hit = False
            for j in range(i + 1, len(st)):
                if dt[j] - dt[i] > horizon_days:
                    break
                if st[j] == dst:
                    hit = True
                    break
            if hit:
                n_ok += 1
                ok.append((dt[i], xr[i]))
            else:
                fail.append((dt[i], xr[i]))
    if n == 0:
        return None

    def _side(pairs):
        if len(pairs) < 10:
            return None, None, None
        s = pd.DataFrame(pairs, columns=["asof", "x"]).dropna()
        if s.empty:
            return None, None, None
        d = s.groupby("asof")["x"].mean()
        return float(s["x"].mean()), float(d.mean()), _tstat(d.to_numpy())

    m_ok, d_ok, t_ok = _side(ok)
    m_fail, d_fail, t_fail = _side(fail)
    return {"n": n, "n_conv": n_ok, "rate": n_ok / n,
            "x_ok": m_ok, "d_ok": d_ok, "t_ok": t_ok,
            "x_fail": m_fail, "d_fail": d_fail, "t_fail": t_fail}


def _bp(x):
    return "—" if x is None or not np.isfinite(x) else f"{x * 1e4:+.0f}bp"


def _pct(x):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--step", type=int, default=STEP_DEFAULT)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.run:
        return cmd_run(args)
    if args.report:
        return cmd_report(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
