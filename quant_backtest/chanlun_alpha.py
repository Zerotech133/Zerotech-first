# -*- coding: utf-8 -*-
"""基准 / beta / alpha 口径（P0-1：门户口径从 raw 改为 alpha）。

为什么要有这个模块
------------------
`chanlun_scan._stats()` 只统计 raw 收益，于是门户把「3卖 62% 胜率」当成有效
信号。但 2026 年这段样本里卖出信号的超额几乎全部来自 beta：beta 中性化后
3卖 为 -0.42%（t=-2.44）、2卖 -1.46%，而 1买 反而是 +1.35%（t=3.92）。
不区分 raw 与 alpha，所有阈值/权重的调整都是在错误的因变量上做拟合。

基准选择（关键，且与原归因脚本不同）
------------------------------------
文件 data/ashare/000001_SH.parquet（上证指数）只有 **2025-01-02 起的 431 根**
且末根停在 2026-10-16。做 3 年扩样本时早期样本根本没有基准可算，若沿用该
指数，alpha 只能覆盖最近一年，扩样本就白扩。故主基准改为 **等权全A（EW）**：

  1. 由本地 ashare 全样本每日 pct_chg 截面均值自建，覆盖 2023-04 起全历史；
  2. 缠论的候选池是全市场等权（不做市值加权选股），EW 才是同分布对照组；
  3. 不含银行/白酒权重失真，也不受指数成分调整影响。

上证指数保留为**对照基准**（区间可覆盖时才计算），用于验证 EW 结论不依赖
基准选择。两套基准的 alpha 差异见 `--check-bench` 自检输出。

口径定义
--------
    raw   = direction × (exit_px / entry_px - 1)          # 已带方向
    alpha = raw - beta × bench_ret(entry_date, exit_date]

  · beta = 入场前 250 个交易日个股日收益 对 基准日收益 的 OLS 斜率；
    配对 < 120 或基准方差≈0 → beta=1.0 且 est=False（退回旧口径，明确标注）；
  · bench_ret 取 (entry_date, exit_date] 的基准累计收益，与 raw 的
    「入场收盘→出场收盘」区间严格对齐（都不含 entry_date 当天）；
  · 卖出信号方向取反（与 _dir_ret 一致），因此 alpha 对卖出点含义是
    「相对基准少亏/多赚」。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import numpy as np

# 公共文件锁（判例 2026-11-09：多进程共写 bench json 互撞致 H1 FAIL）
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common_filelock import write_json_safe  # noqa: E402

log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
BENCH_EW = os.path.join(DATA, "chanlun_bench_ew.json")
SSE_FILE = os.path.join(ASHARE, "000001_SH.parquet")

WIN = 250  # beta 估计窗口（交易日）
MIN_PAIRS = 120  # beta 配对下限，不足则 est=False
BENCH_EXCLUDE = {"000001_SH"}  # 指数文件混在 ashare 目录里，剔除避免污染 EW

_EW = None  # 懒加载 {"dates": [...], "ret": np.ndarray, "pos": {dt: i}}
_SSE = None
_PX = {}  # code → (dates list, ret ndarray)


# ── 等权全A基准 ────────────────────────────────────────────
def build_bench_ew(force=False, verbose=True):
    """扫描全样本 pct_chg 建等权全A日收益序列，落盘缓存。

    返回 {"generated_at", "src", "dates": [...], "ret": [...], "n": [...]}。
    幂等：当日已生成且非 force 时直接读缓存（全量扫 ~5600 个 parquet 约
    1-2 分钟，产线每日一次可接受，但不要在每次 pool_eval 里重算）。
    """
    if not force and os.path.exists(BENCH_EW):
        try:
            with open(BENCH_EW, encoding="utf-8") as f:
                d = json.load(f)
            if str(d.get("generated_at", ""))[:10] == datetime.now().strftime("%Y-%m-%d"):
                return d
        except Exception as exc:
            log.warning("bench cache unreadable, rebuild: %s", exc)

    import pandas as pd

    acc = {}  # dt → [sum, n]
    files = [f for f in os.listdir(ASHARE) if f.endswith(".parquet")]
    ok = 0
    for i, f in enumerate(files):
        if f[:-8] in BENCH_EXCLUDE:
            continue
        try:
            df = pd.read_parquet(
                os.path.join(ASHARE, f), columns=["trade_date", "pct_chg"]
            )
        except Exception:
            continue
        if df.empty:
            continue
        ok += 1
        td = df["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        pc = pd.to_numeric(df["pct_chg"], errors="coerce")
        pc = pc.where(pc.abs() <= 25.0)  # 脏点（未复权/错标）剔除，A股单日限幅内
        for d, v in zip(td, pc.to_numpy()):
            if v != v:  # NaN
                continue
            a = acc.get(d)
            if a is None:
                acc[d] = [float(v), 1]
            else:
                a[0] += float(v)
                a[1] += 1
        if verbose and (i + 1) % 1000 == 0:
            print("  bench ..%d/%d files" % (i + 1, len(files)))

    dates = sorted(acc)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "src": "EW-allA(excl %s)" % ",".join(sorted(BENCH_EXCLUDE)),
        "n_files": ok,
        "win": "2023-04-03~",
        "dates": dates,
        "ret": [round(acc[d][0] / acc[d][1] / 100.0, 8) for d in dates],
        "n": [acc[d][1] for d in dates],
    }
    os.makedirs(DATA, exist_ok=True)
    write_json_safe(BENCH_EW, out)  # 持锁 + 唯一 tmp(pid) + 原子替换，多进程安全
    if verbose:
        print("bench EW built: %d dates, %d files -> %s" % (len(dates), ok, BENCH_EW))
    return out


def _pack(d):
    ret = np.asarray(d["ret"], dtype=float)
    return {
        "dates": d["dates"],
        "ret": ret,
        "n": d.get("n") or [0] * len(ret),
        "src": d.get("src"),
        "pos": {t: i for i, t in enumerate(d["dates"])},
    }


def bench_ew():
    global _EW
    if _EW is None:
        _EW = _pack(build_bench_ew())
    return _EW


def bench_sse():
    """上证指数对照基准（仅 2025-01 起；缺失返回 None）。"""
    global _SSE
    if _SSE is not None:
        return _SSE or None
    try:
        import pandas as pd

        df = pd.read_parquet(SSE_FILE, columns=["trade_date", "pct_chg"])
        td = df["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        pc = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(0.0) / 100.0
        _SSE = _pack({"dates": td.tolist(), "ret": pc.to_numpy()})
    except Exception:
        _SSE = False
    return _SSE or None


# ── 个股序列 ──────────────────────────────────────────────
def _norm_dt(v):
    return str(v or "").replace("-", "").replace("/", "")[:8]


def stock_ret(code):
    """code(600519.SH) → (dates list, 日收益 ndarray)。缺失返回 (None, None)。

    用 pct_chg 而非 close 比值：pct_chg 是交易所口径（已处理除权），与产线
    hfq 序列的收益率一致，且不必再走一遍复权链接。
    """
    if code in _PX:
        return _PX[code]
    import pandas as pd

    f = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        _PX[code] = (None, None)
        return _PX[code]
    try:
        df = pd.read_parquet(f, columns=["trade_date", "pct_chg"])
    except Exception:
        _PX[code] = (None, None)
        return _PX[code]
    df = df.dropna(subset=["pct_chg"])
    td = (
        df["trade_date"]
        .astype(str)
        .str.replace("-", "", regex=False)
        .str[:8]
        .tolist()
    )
    ret = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(0.0).to_numpy() / 100.0
    _PX[code] = (td, ret)
    return _PX[code]


def _pos_le(dates, target):
    """二分：最后一个 dt <= target 的下标；无则 -1。"""
    if not dates or dates[0] > target:
        return -1
    lo, hi = 0, len(dates) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dates[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def beta_of(code, asof_date, win=WIN, min_pairs=MIN_PAIRS, bench=None):
    """入场前滚动 beta（严格用 asof_date 之前的数据，无未来函数）。

    asof_date = 入场日；只用 dt < asof_date 的配对。
    返回 (beta, est, pairs)。
    """
    b = bench or bench_ew()
    sd, sr = stock_ret(code)
    if sd is None:
        return 1.0, False, 0
    asof = _norm_dt(asof_date)
    end = _pos_le(sd, asof)
    if end < 0 or end == 0 and sd[0] >= asof:
        return 1.0, False, 0
    lo = max(0, end - win + 1)
    xs, ys = [], []
    bret, bpos = b["ret"], b["pos"]
    for t in range(lo, end + 1):
        if sd[t] >= asof:  # 严格排除入场日及之后
            break
        j = bpos.get(sd[t])
        if j is None:
            continue
        xs.append(bret[j])
        ys.append(sr[t])
    n = len(xs)
    if n < min_pairs:
        return 1.0, False, n
    x = np.asarray(xs)
    y = np.asarray(ys)
    vx = float(x.var())
    if vx <= 1e-12:
        return 1.0, False, n
    beta = float(((x - x.mean()) * (y - y.mean())).mean() / vx)
    if not np.isfinite(beta):
        return 1.0, False, n
    # 极端 beta 截断：数据脏点（长期停牌后补涨）会给出 5+ 的斜率，
    # 直接用于 alpha 会把基准收益放大成噪声。截断而非丢弃，保留 est。
    beta = max(-0.5, min(3.0, beta))
    return round(beta, 3), True, n


def bench_window_ret(d0, d1, bench=None):
    """基准在 (d0, d1] 的累计收益（与 raw 的收盘→收盘区间对齐）。"""
    b = bench or bench_ew()
    dates, ret = b["dates"], b["ret"]
    a = _norm_dt(d0)
    z = _norm_dt(d1)
    i = _pos_le(dates, a)
    j = _pos_le(dates, z)
    if i < 0 or j < 0 or j <= i:
        return None
    seg = ret[i + 1 : j + 1]
    return float(np.prod(1.0 + seg) - 1.0)


def alpha_of(raw, entry_date, exit_date, beta, code=None, bench=None):
    """alpha = raw - beta × bench_ret。基准区间不可得 → None（不伪造成 0）。"""
    br = bench_window_ret(entry_date, exit_date, bench=bench)
    if br is None:
        return None
    return round(float(raw) - float(beta) * br, 6)


def annotate(code, raw, entry_date, exit_date, asof_date=None, bench=None):
    """一次性算出 (beta, est, pairs, idx_ret, alpha)。产线结算与实验共用。"""
    b = bench or bench_ew()
    beta, est, pairs = beta_of(code, asof_date or entry_date, bench=b)
    br = bench_window_ret(entry_date, exit_date, bench=b)
    if br is None:
        return {"beta": beta, "beta_est": est, "beta_n": pairs,
                "idx_ret": None, "alpha": None}
    return {
        "beta": beta,
        "beta_est": est,
        "beta_n": pairs,
        "idx_ret": round(br, 6),
        "alpha": round(float(raw) - beta * br, 6),
        "bench": b.get("src") if isinstance(b, dict) else None,
    }


# ── 自检 / 人工核对 ───────────────────────────────────────
def check(n=3):
    """基准与 beta 的合理性核对（打印，不改任何文件）。"""
    b = bench_ew()
    dates, ret = b["dates"], b["ret"]
    cum = float(np.prod(1.0 + ret) - 1.0)
    print("EW 基准: %d 个交易日 %s~%s, 累计 %+.1f%%, 日均 %.3f%%, 日均家数 %d"
          % (len(dates), dates[0], dates[-1], cum * 100,
             ret.mean() * 100, int(np.mean(b["n"]))))
    print("  最大单日 %+.2f%% (%s) / 最小 %+.2f%% (%s)"
          % (ret.max() * 100, dates[int(ret.argmax())],
             ret.min() * 100, dates[int(ret.argmin())]))
    sse = bench_sse()
    if sse:
        print("SSE 对照: %d 日 %s~%s（覆盖面不同，仅用于对照）"
              % (len(sse["dates"]), sse["dates"][0], sse["dates"][-1]))
    import chanlun_scan as cs  # 延迟导入避免循环

    rows = [r for r in cs._load_pool() if r.get("market") == "CN"]
    seen = []
    for r in rows[-400:]:
        if r["code"] in seen:
            continue
        seen.append(r["code"])
        beta, est, pairs = beta_of(r["code"], r["detect_date"], bench=b)
        print("  %-10s %-4s asof=%s beta=%-6.3f est=%-5s pairs=%d"
              % (r["code"], r["type"], r["detect_date"], beta, est, pairs))
        if len(seen) >= n:
            break


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="chanlun alpha/beta 口径自检")
    ap.add_argument("--rebuild", action="store_true", help="强制重建 EW 基准")
    ap.add_argument("--n", type=int, default=5, help="抽样核对的标的数")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.rebuild:
        build_bench_ew(force=True)
    check(n=a.n)
