# -*- coding: utf-8 -*-
"""strong 时间窗共振 · 离线回测调研（2026-10-11，T1 优化阶段3/D4 决策支持）。

背景：评估报告显示 strong 领涨(154) × chanlun 买点(27) 当日交集仅 2（1.3%），
strong=动量追涨 / chanlun=回调低吸 同日互斥。本调研检验 D4 假设——
strong 信号加 5-10 日衰减窗参与共振：「N 日内曾板块领涨 ∩ 当日缠论买点」
交集能否提升 ≥3 倍且窗口收益不劣。

口径（与生产同源，零生产改动）：
  · 样本 = chanlun_pool.jsonl 全部 CN 个股买点事件（type ∈ 1买/2买/3买），
    基准日 D = detect_date（检测批次日，即线上 T1 画像日）
  · strong_verdict 逐日重放（复刻 strong_scan.eval_stock / linkage.series）：
      ex250[d] = (个股d/个股d-249 - 1)*100 - (板块d/板块d-249 - 1)*100   # 公共首根基准
      领涨[d] = ex250[d] ≥ +10pp 且 板块成员 ≥5 且 个股日线多头 last>ma20>ma60
               且 K线数 ≥250（次新降级观察，同 strong_scan S2 修复）
      个股日线 ts_daily 湖（2023-07 起，未复权）；板块 tdx_sector_daily
      （2021-01 起）；板块按个股时间轴前向填充（merge_asof backward）
  · 「N日内曾领涨」= D 及前 N-1 个交易日（共 N 个交易日）任一日领涨
  · 窗口收益 = build_pick_verdicts.pct_between（生产同口径）：
      exec = D 后首交易日开盘买入 × T+5/T+10 收盘卖出；
      ts_adj_factor 复权，因子缺则 -30.5% 缺口守卫 void（"exdiv"）

输出：交集提升倍数 + 窗口收益对比 + N∈{3,5,10} 敏感性。

用法：python strong_ts_window_research.py
"""

import bisect
import collections
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "portal"))

import linkage  # noqa: E402
import tdx_read  # noqa: E402

POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
BUY_TYPES = ("1买", "2买", "3买")
N_SENS = (3, 5, 10)
WINDOWS = (5, 10)
EX_TH = 10.0  # strong_track.STRONG_EX_TH
MIN_MEMBERS = 5  # strong_track.MIN_BLOCK_MEMBERS
MIN_BARS = 250  # 次新闸（strong_scan S2 修复）
CAL_START = "20200101"
DATA_START = "20230101"


def d8(s):
    return str(s).replace("-", "")[:8]


def load_events():
    """chanlun_pool 买点事件（CN，type ∈ 买三型）。"""
    evts = []
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("market") != "CN":
                continue
            if r.get("type") not in BUY_TYPES:
                continue
            code = str(r.get("code") or "").strip()
            dd = str(r.get("detect_date") or "")[:10]
            if not code or len(dd) != 10:
                continue
            evts.append(
                {
                    "code": code,
                    "type": r.get("type"),
                    "dt": str(r.get("dt") or "")[:10],
                    "detect_date": dd,
                }
            )
    return evts


def trade_calendar():
    df = tdx_read.read_range("ts_daily", CAL_START, "20261231", columns=["trade_date"])
    days = sorted({d8(d) for d in df["trade_date"].tolist() if d8(d).isdigit()})
    return days


def after_days(days, sig, n):
    """sig(YYYYMMDD) 后第 n 个交易日（严格大于，与 build_pick_verdicts 同义）。"""
    i = bisect.bisect_right(days, sig)
    j = i + n - 1
    return days[j] if j < len(days) else None


def fast_pct(stock, adjmap, exec_day, d1):
    """生产同口径窗口收益（build_pick_verdicts.pct_between 逻辑重实现，
    用已全量加载的分组数据，避免逐事件 read_range 全表扫描）。

    exec_day 开盘买入 → d1 收盘卖出；ts_adj_factor 区间因子齐备时
    buy=open0×adj0 / sel=close-1×adj-1；缺因子降级 -30.5% 缺口守卫，
    判除权返回 "exdiv"（与 pct_between 同义）。
    """
    df = stock[(stock["trade_date"] >= exec_day) & (stock["trade_date"] <= d1)]
    if len(df) < 1:
        return None
    df = df.sort_values("trade_date")
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    if not (np.isfinite(opens[0]) and np.isfinite(closes[-1])):
        return None  # 湖脏行（NaN 价）：不可算，不入桶
    adjs = None
    if adjmap is not None:
        adjs = [adjmap.get(str(t)) for t in df["trade_date"].tolist()]
    if adjs is not None and all(a is not None and np.isfinite(a) for a in adjs):
        buy = opens[0] * adjs[0]
        sel = closes[-1] * adjs[-1]
        return round((sel / buy - 1) * 100, 2) if buy else None
    for k in range(1, len(df)):
        prev = closes[k - 1]
        if prev and opens[k] / prev - 1 <= -0.305:
            return "exdiv"
    buy = opens[0]
    sel = closes[-1]
    return round((sel / buy - 1) * 100, 2) if buy else None


def load_table_grouped(tbl, cols, code_col, date_col="trade_date"):
    """全表分片 concat → {code: df[['trade_date', ...]] 升序}。"""
    parts = sorted(glob.glob(os.path.join(HERE, "data", "tdx", tbl, "part_*.parquet")))
    frames = [pd.read_parquet(p, columns=cols) for p in parts]
    df = pd.concat(frames, ignore_index=True)
    df[date_col] = df[date_col].astype(str).str[:8]
    out = {}
    for c, g in df.groupby(code_col):
        out[str(c)] = g.sort_values(date_col).reset_index(drop=True)
    return out


def build_lead(stock, blk, n_mem):
    """逐日滚动重放 strong_verdict → (dates, lead 布尔序列)。"""
    a = stock[["trade_date", "close"]].copy()
    b = blk[["trade_date", "close"]].rename(columns={"close": "bclose"})
    # merge_asof 要求数值 on 列；'YYYYMMDD' 转 int 保持时间序
    a["td"] = a["trade_date"].astype("int64")
    b["td"] = b["trade_date"].astype("int64")
    b = b[["td", "bclose"]]
    m = pd.merge_asof(a, b, on="td", direction="backward")
    close = m["close"].to_numpy(dtype=float)
    bclose = m["bclose"].to_numpy(dtype=float)
    dates = m["trade_date"].tolist()
    s = pd.Series(close)
    bs = pd.Series(bclose)
    ex = (
        (s / s.shift(MIN_BARS - 1) - 1) * 100 - (bs / bs.shift(MIN_BARS - 1) - 1) * 100
    ).to_numpy(dtype=float)
    ma20 = s.rolling(20).mean().to_numpy(dtype=float)
    ma60 = s.rolling(60).mean().to_numpy(dtype=float)
    bull = (close > ma20) & (ma20 > ma60)
    n_bars = np.arange(1, len(close) + 1)
    lead = (n_bars >= MIN_BARS) & (ex >= EX_TH) & bull & (n_mem >= MIN_MEMBERS)
    return dates, lead


def main():
    evts = load_events()
    print("[1/5] 买点事件 %d" % len(evts), flush=True)
    by_code = collections.defaultdict(list)
    for e in evts:
        by_code[e["code"]].append(e)

    cal = trade_calendar()
    print("[2/5] 交易日历 %s ~ %s（%d 天）" % (cal[0], cal[-1], len(cal)), flush=True)

    stocks = load_table_grouped(
        "ts_daily", ["ts_code", "trade_date", "open", "close"], "ts_code"
    )
    blks = load_table_grouped(
        "tdx_sector_daily", ["code", "trade_date", "close"], "code"
    )
    adj_all = load_table_grouped(
        "ts_adj_factor", ["ts_code", "trade_date", "adj_factor"], "ts_code"
    )
    print(
        "[3/5] ts_daily %d 票 / tdx_sector_daily %d 板块 / "
        "ts_adj_factor %d 票" % (len(stocks), len(blks), len(adj_all)),
        flush=True,
    )

    rows, n_skip = [], 0
    resolved = {}
    for code, evts_c in by_code.items():
        if code not in resolved:
            resolved[code] = linkage.resolve_block(code)
        blk = resolved[code]
        if not blk:
            n_skip += 1
            continue
        bc = blk["code"]
        stock = stocks.get(code)
        bdf = blks.get(bc)
        if stock is None or bdf is None or len(stock) < MIN_BARS:
            n_skip += 1
            continue
        n_mem = len(linkage.block_members(bc))
        dates, lead = build_lead(stock, bdf, n_mem)
        adf = adj_all.get(code)
        adjmap = (
            {str(t): float(a) for t, a in zip(adf["trade_date"], adf["adj_factor"])}
            if adf is not None and len(adf)
            else None
        )
        for e in evts_c:
            D = d8(e["detect_date"])
            i = bisect.bisect_right(dates, D) - 1
            if i < 0:
                continue
            today = bool(lead[i])
            nlead = {N: bool(lead[max(0, i - N + 1) : i + 1].any()) for N in N_SENS}
            exec_day = after_days(cal, D, 2)  # 生产口径：D+2 首次可操作
            if exec_day is None:
                continue
            wins = {}
            for w in WINDOWS:
                d1 = after_days(cal, exec_day, w)
                wins[w] = fast_pct(stock, adjmap, exec_day, d1) if d1 else None
            row = {
                "code": code,
                "D": D,
                "today": today,
                "n3": nlead[3],
                "n5": nlead[5],
                "n10": nlead[10],
                "wins": wins,
            }
            rows.append(row)
    print(
        "[4/5] 事件处理完成：%d 行（板块解析失败/数据不足跳过 %d）"
        % (len(rows), n_skip),
        flush=True,
    )

    df = pd.DataFrame(rows)
    n_total = len(df)
    n_today = int(df["today"].sum())
    print("[5/5] 汇总", flush=True)

    def stats(sel):
        out = {}
        for w in WINDOWS:
            # pandas .map 把 None 自动转 NaN（isinstance(nan,float)=True），
            # 必须 isfinite 过滤，否则待满窗样本污染均值/中位
            vals = [
                r
                for r in sel["wins"].map(lambda d: d.get(w))
                if isinstance(r, float) and np.isfinite(r)
            ]
            n_pending = int(
                sum(
                    1
                    for r in sel["wins"].map(lambda d: d.get(w))
                    if r is None or (isinstance(r, float) and np.isnan(r))
                )
            )
            n_void = int(
                sum(1 for r in sel["wins"].map(lambda d: d.get(w)) if r == "exdiv")
            )
            n = len(vals)
            if not n:
                out[w] = (None, None, None, 0, n_void, n_pending)
                continue
            arr = np.array(vals)
            winr = float((arr > 0).mean() * 100)
            out[w] = (
                round(float(arr.mean()), 2),
                round(float(np.median(arr)), 2),
                round(winr, 1),
                n,
                n_void,
                n_pending,
            )
        return out

    groups = {
        "全买点池": (df.index, n_total),
        "当日领涨": (df.index[df["today"]], n_today),
    }
    for N in N_SENS:
        k = "n%d" % N
        groups["N=%d 曾领涨" % N] = (df.index[df[k]], int(df[k].sum()))

    print("\n=== strong 时间窗共振 · 交集与窗口收益 ===")
    print(
        "%-14s %8s %10s | %12s %12s %12s %8s | %12s %12s %12s %8s"
        % (
            "组别",
            "事件数",
            "占全池%",
            "T+5均值",
            "T+5中位",
            "T+5胜率%",
            "void",
            "T+10均值",
            "T+10中位",
            "T+10胜率%",
            "void",
        )
    )
    for name, (idx, n) in groups.items():
        g = df.loc[idx]
        s = stats(g)
        mult = ("x%.1f" % (n / n_today)) if n_today and name != "全买点池" else "-"
        f5, f10 = s[5], s[10]

        def fmt(x):
            if x[0] is None:
                return "  --  "
            return "%9.2f %9.2f %8.1f %5d" % x

        print(
            "%-14s %8d %6.1f%%(%s) | %s | %s"
            % (
                name,
                n,
                n / n_total * 100,
                mult,
                fmt((f5[0], f5[1], f5[2], f5[3])),
                fmt((f10[0], f10[1], f10[2], f10[3])),
            )
        )

    print("\n=== D4 决策对照 ===")
    base5, base10 = stats(df)[5], stats(df)[10]
    for N in N_SENS:
        k = "n%d" % N
        g = df.loc[df[k]]
        s5, s10 = stats(g)[5], stats(g)[10]
        print(
            "N=%2d 曾领涨: %4d 事件 (当日%d 的 %.1fx) | "
            "T+5 中位 %s vs 全池 %s | T+10 中位 %s vs 全池 %s"
            % (
                N,
                int(g[k].sum()),
                n_today,
                int(g[k].sum()) / max(1, n_today),
                "%.2f" % s5[1] if s5[1] is not None else "--",
                "%.2f" % base5[1] if base5[1] is not None else "--",
                "%.2f" % s10[1] if s10[1] is not None else "--",
                "%.2f" % base10[1] if base10[1] is not None else "--",
            )
        )

    # 逐日领涨状态分布核对（当日比例 vs 评估报告单日口径）
    print(
        "\n当日领涨占买点池比例：%.1f%%（评估报告单日 2/27=7.4%%；本回放无"
        "生产 strong 扫描的每板块前 20 截断，比例口径偏高属预期）"
        % (n_today / n_total * 100)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
