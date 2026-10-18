# -*- coding: utf-8 -*-
"""雪球舆情因子有效性预演探针 v0.1（只读·链路验证）。

背景（2026-10-18 R3 事件风险校准预演）：
- 时间轴对齐核查发现：雪球存量 14212 条为历史深挖数据（2017-02~2023-11），
  RiskGuard R3 拦截账（ledger.jsonl）集中在 2026-09~10 → 「R3 拦截×舆情」
  历史关联两侧零重叠不可行；改道做姊妹研究——舆情因子历史有效性，
  为未来 R3 引入舆情信号提供权重校准依据。
- 价格口径：ts_daily 为月末截面采样（47 片，2023-07 起）不支撑 fwd5/fwd20；
  ts_monthly 全历史月线（40.7 万行/6019 只，2020-01~2026-09）可用 →
  因子语义定为 **fwd_next_m = 帖子次月收益（下月末 close / 当月末 close - 1）**，
  无前视（发帖时间真实，收益从下月起算）。

样本现状：分类回填 385/14212，其中 v1.1 修复后带纯 6 位 A 股代码的帖子
  有限——本版仅验证链路，n 小无统计意义；追平后重跑即全量研究。

口径细节：
- ticker→ts_code：纯 6 位数字，6 开头→.SH，0/3 开头→.SZ，其余跳过（v1.2 白名单待做）
- 月索引：trade_date 形如 20260930 → ym=202609；帖子月 ym 在月线序列中
  需同时存在 ym 与下一 ym 两根 bar，否则 skip（月线末段/缺月）
- 复权：ts_monthly 未复权（月度粗口径可接受；全量版可换 ts_stk_week_month_adj）

用法（系统 3.11，pandas 判例）：
    C:/Program Files/Python311/python.exe quant_backtest/tools/research/xq_sentiment_factor_probe.py
"""
from __future__ import annotations

import glob
import json
import sqlite3
from bisect import bisect_left
from collections import defaultdict

DB = "C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db"
MONTHLY_GLOB = "C:/项目/老田的AI助手/quant_backtest/data/tdx/ts_monthly/part_*.parquet"


def load_monthly():
    import pandas as pd
    parts = sorted(glob.glob(MONTHLY_GLOB))
    df = pd.concat([pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
                    for p in parts], ignore_index=True)
    df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["trade_date", "close"])
    df["ym"] = df["trade_date"].astype(int) // 100
    out = {}
    for code, g in df.groupby("ts_code"):
        g = g.sort_values("ym").drop_duplicates("ym")
        out[code] = (g["ym"].tolist(), g["close"].tolist())
    return out, len(df)


def ym_of(created_at: str) -> int:
    try:
        d = str(created_at)[:10].replace("-", "")
        return int(d[:6])
    except (ValueError, TypeError):
        return 0


def fwd_next_m(monthly, code, ym):
    """帖子月 ym → 次月收益%。需 ym 与 ym+1 两根月线 bar（跨年用 13 逻辑换算）。"""
    ent = monthly.get(code)
    if not ent:
        return None, "no_code"
    yms, closes = ent
    p = bisect_left(yms, ym)
    if p >= len(yms) or yms[p] != ym:
        return None, "no_bar"
    nxt = ym + 1 if ym % 100 < 12 else ym - 99  # 202612 → 202701
    if p + 1 < len(yms) and yms[p + 1] == nxt:
        base, end = closes[p], closes[p + 1]
        if base and end:
            return round((end / base - 1) * 100, 2), None
    return None, "no_next"


def to_ts_code(t: str):
    """纯 6 位数字 → ts_code；6→.SH，0/3→.SZ，其余 None。"""
    if not (t.isdigit() and len(t) == 6):
        return None
    if t.startswith("6"):
        return t + ".SH"
    if t.startswith(("0", "3")):
        return t + ".SZ"
    return None


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    rows = con.execute(
        """SELECT c.tickers, c.sentiment, c.event_type, s.created_at
           FROM xq_status_classified c JOIN xq_status s USING (status_id)
           WHERE c.parse_ok=1 AND c.tickers IS NOT NULL AND c.tickers != '[]'"""
    ).fetchall()
    con.close()

    monthly, n_mrows = load_monthly()
    print("带 ticker 帖 %d 条 × 月线 %d 行 %d 只" % (len(rows), n_mrows, len(monthly)))

    skip = defaultdict(int)
    samples = []  # (ts_code, ym, sentiment, event_type, fwd)
    no_map_posts = 0  # 漏斗：无任何可映射个股代码的帖子数（基金词/指数词为主）
    for tickers, senti, etype, created in rows:
        ym = ym_of(created)
        if not ym:
            skip["bad_date"] += 1
            continue
        mapped = None
        for t in json.loads(tickers):
            code = to_ts_code(t)
            if not code:
                continue
            fwd, why = fwd_next_m(monthly, code, ym)
            if why:
                skip[why] += 1
                continue
            mapped = (code, ym, senti, etype, fwd)
            break  # 每帖取首个可对齐标的
        if mapped:
            samples.append(mapped)
        else:
            no_map_posts += 1

    print("可算样本 %d 条（无映射帖 %d），跳过 %s"
          % (len(samples), no_map_posts, dict(skip) or "{}"))
    if not samples:
        print("（样本为零=链路两端均空，属预演预期——追平后重跑）")
        return

    by_senti = defaultdict(list)
    for _, _, senti, _, fwd in samples:
        by_senti[senti].append(fwd)
    print("\n== 舆情情绪 → 次月收益%（链路验证口径·n 小勿拍板）==")
    for s in ("bullish", "neutral", "bearish"):
        v = by_senti.get(s, [])
        if v:
            v2 = sorted(v)
            win = sum(1 for x in v if x > 0) / len(v) * 100
            print("%-8s n=%-4d mean=%+.2f median=%+.2f win=%.0f%%"
                  % (s, len(v), sum(v) / len(v), v2[len(v2) // 2], win))
    missing_bear = ("bearish" in by_senti) is False
    if missing_bear:
        print("（bearish 组暂空——样本不足）")

    by_ym = defaultdict(list)
    for _, ym, _, _, fwd in samples:
        by_ym[ym].append(fwd)
    print("\n按月分布:", {k: len(v) for k, v in sorted(by_ym.items())})


if __name__ == "__main__":
    main()
