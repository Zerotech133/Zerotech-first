# -*- coding: utf-8 -*-
"""雪球舆情因子有效性预演探针 v0.2（只读·content_type 分层增强版）。

背景（2026-10-18 R3 事件风险校准预演）：
- 时间轴对齐核查发现：雪球存量为历史深挖数据（2017-02~2023-11），
  RiskGuard R3 拦截账（ledger.jsonl）集中在 2026-09~10 → 「R3 拦截×舆情」
  历史关联两侧零重叠不可行；改道做姊妹研究——舆情因子历史有效性，
  为未来 R3 引入舆情信号提供权重校准依据。
- 价格口径：ts_monthly 全历史月线（40.7 万行/6019 只，2020-01~2026-09）可用 →
  因子语义定为 **fwd_next_m = 帖子次月收益（下月末 close / 当月末 close - 1）**，
  无前视（发帖时间真实，收益从下月起算）。

v0.2 变更（分类管道 v1.3 上线后）：
- 样本口径：parse_ok=1 AND content_type NOT IN ('ad','edu')（干净信号，
  ad 行已由 v1.3 规则短路归位，edu 为教学贴非标的观点）；
  content_type 为 NULL 的 v1.1 存量行保留，单独记为 untyped 层。
- content_type 分层因子：tech / fundamental / news（及 community / untyped）
  各层独立统计 bullish 帖 vs bearish 帖的次月收益差（bull - bear）。
- 分月稳定性：按帖子月份分桶看 bull-bear 收益差的符号一致性。
- 漏斗计数完整保留（ad/edu 排除、no_ticker、bad_date、no_map、
  no_code/no_bar/no_next 等跳过原因）。

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

# 干净信号口径：排除广告与教学贴；NULL（v1.1 存量）保留为 untyped 层
EXCLUDE_TYPES = ("ad", "edu")
LAYERS = ("fundamental", "news", "community", "tech", "untyped")
MIN_N = 10  # 层样本低于此数标注「样本不足」


def load_monthly():
    import pandas as pd

    parts = sorted(glob.glob(MONTHLY_GLOB))
    df = pd.concat(
        [pd.read_parquet(p, columns=["ts_code", "trade_date", "close"]) for p in parts],
        ignore_index=True,
    )
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


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def median(v):
    v2 = sorted(v)
    return v2[len(v2) // 2] if v2 else float("nan")


def layer_of(ctype):
    """content_type → 分层名；NULL → untyped（v1.1 存量）。"""
    if ctype is None or ctype == "":
        return "untyped"
    return str(ctype)


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    rows = con.execute(
        """SELECT c.tickers, c.sentiment, c.content_type, s.created_at
           FROM xq_status_classified c JOIN xq_status s USING (status_id)
           WHERE c.parse_ok=1"""
    ).fetchall()
    con.close()

    monthly, n_mrows = load_monthly()
    print("parse_ok=1 帖 %d 条 × 月线 %d 行 %d 只" % (len(rows), n_mrows, len(monthly)))

    # ---------- 漏斗 ----------
    skip = defaultdict(int)  # 帖级跳过原因
    tick_skip = defaultdict(int)  # ticker 级跳过原因（一帖多票可能多次计数）
    samples = []  # (ts_code, ym, sentiment, layer, fwd)
    for tickers, senti, ctype, created in rows:
        layer = layer_of(ctype)
        if layer in EXCLUDE_TYPES:
            skip[layer] += 1
            continue
        if not tickers or tickers == "[]":
            skip["no_ticker"] += 1
            continue
        ym = ym_of(created)
        if not ym:
            skip["bad_date"] += 1
            continue
        mapped = None
        for t in json.loads(tickers):
            code = to_ts_code(t)
            if not code:
                tick_skip["bad_fmt"] += 1
                continue
            fwd, why = fwd_next_m(monthly, code, ym)
            if why:
                tick_skip[why] += 1
                continue
            mapped = (code, ym, senti, layer, fwd)
            break  # 每帖取首个可对齐标的
        if mapped:
            samples.append(mapped)
        else:
            skip["no_map"] += 1  # 无任何可映射且可对齐的个股

    print("可算样本 %d 条；帖级跳过 %s" % (len(samples), dict(skip) or "{}"))
    print("（ticker 级跳过 %s）" % (dict(tick_skip) or "{}"))
    if not samples:
        print("（样本为零=链路两端均空，属预演预期——追平后重跑）")
        return

    # ---------- 总体情绪 → 次月收益 ----------
    by_senti = defaultdict(list)
    for _, _, senti, _, fwd in samples:
        by_senti[senti].append(fwd)
    print("\n== 总体：舆情情绪 → 次月收益%（n 小勿拍板）==")
    for s in ("bullish", "neutral", "bearish"):
        v = by_senti.get(s, [])
        if v:
            win = sum(1 for x in v if x > 0) / len(v) * 100
            print(
                "%-8s n=%-4d mean=%+.2f median=%+.2f win=%.0f%%"
                % (s, len(v), mean(v), median(v), win)
            )
    if "bearish" not in by_senti:
        print("（bearish 组暂空——样本不足）")

    # ---------- content_type 分层因子 ----------
    by_layer = defaultdict(lambda: defaultdict(list))
    for _, _, senti, layer, fwd in samples:
        by_layer[layer][senti].append(fwd)
    print("\n== content_type 分层：bullish 均值 − bearish 均值 = bull-bear 差 % ==")
    print(
        "%-12s %5s %6s %6s %9s %9s %9s %s"
        % ("layer", "n", "n_bull", "n_bear", "bull%", "bear%", "diff", "备注")
    )
    for layer in LAYERS:
        d = by_layer.get(layer)
        if not d:
            continue
        nb = d.get("bullish", [])
        nr = d.get("bearish", [])
        n = sum(len(v) for v in d.values())
        if n < MIN_N or not nb or not nr:
            print(
                "%-12s %5d %6d %6d %9s %9s %9s %s"
                % (
                    layer,
                    n,
                    len(nb),
                    len(nr),
                    "-",
                    "-",
                    "-",
                    "样本不足（缺 bull/bear 一侧或 n<%d）" % MIN_N,
                )
            )
            continue
        diff = mean(nb) - mean(nr)
        print(
            "%-12s %5d %6d %6d %9.2f %9.2f %+9.2f %s"
            % (
                layer,
                n,
                len(nb),
                len(nr),
                mean(nb),
                mean(nr),
                diff,
                "样本不足" if n < MIN_N else "",
            )
        )

    # ---------- 分月稳定性（bull-bear 差符号一致性） ----------
    def monthly_diff(samples_sub):
        """按帖子月份分桶 → {ym: (n_bull, n_bear, diff)}，仅保留两侧均有样本的月。"""
        buckets = defaultdict(lambda: defaultdict(list))
        for _, ym, senti, _, fwd in samples_sub:
            buckets[ym][senti].append(fwd)
        out = {}
        for ym in sorted(buckets):
            nb = buckets[ym].get("bullish", [])
            nr = buckets[ym].get("bearish", [])
            if nb and nr:
                out[ym] = (len(nb), len(nr), mean(nb) - mean(nr))
        return out

    print("\n== 分月稳定性：bull-bear 收益差（总体，仅两侧均有样本的月份）==")
    md = monthly_diff(samples)
    pos = neg = 0
    for ym, (nb, nr, diff) in md.items():
        sign = "+" if diff > 0 else ("-" if diff < 0 else "0")
        pos += diff > 0
        neg += diff < 0
        print("  %d: n_bull=%d n_bear=%d diff=%+.2f  [%s]" % (ym, nb, nr, diff, sign))
    if md:
        print("  符号一致性：正 %d 月 / 负 %d 月（共 %d 月）" % (pos, neg, len(md)))
    else:
        print("  （无同时含 bull 与 bear 样本的月份）")

    for layer in ("fundamental", "news"):  # 仅对任务关注且样本尚可的层做分月
        d = by_layer.get(layer) or {}
        if sum(len(v) for v in d.values()) < MIN_N:
            continue
        md_l = monthly_diff(
            [(c, ym, s, l, f) for c, ym, s, l, f in samples if l == layer]
        )
        if not md_l:
            print("  layer=%s：无两侧均有样本的月份（样本不足）" % layer)
            continue
        lp = sum(1 for _, _, x in md_l.values() if x > 0)
        ln = sum(1 for _, _, x in md_l.values() if x < 0)
        print(
            "  layer=%s：正 %d 月 / 负 %d 月（共 %d 月）" % (layer, lp, ln, len(md_l))
        )

    # ---------- 按月分布 ----------
    by_ym = defaultdict(list)
    for _, ym, _, _, fwd in samples:
        by_ym[ym].append(fwd)
    print("\n按月分布:", {k: len(v) for k, v in sorted(by_ym.items())})


if __name__ == "__main__":
    main()
