# -*- coding: utf-8 -*-
"""量能信号历史回放：验证「放量突破」这类规则在历史上是否真有区分度（2026-11-16 WO-VOL-BACKTEST）。

## 为什么先做这个而不是先跑模型

规则层（`volume_price` / `breakout_with_volume` / `shrink_pullback`）是**纯计算**，可以脱离 LLM
用历史数据回放——成本几乎为零。先验证「信号本身有没有用」，再去验证「席位判断有没有用」，
顺序才合理：**如果信号本身是噪声，再好的席位也只是在噪声上做判断**。

## 做法

对每只标的取近 N 个交易日的 `ts_daily`，逐日计算：
- 量比（当日量 / 前 20 日均量，不含当日）
- `breakout_with_volume`：涨 >3% 且量比 ≥1.5 且收盘 > 前 20 日最高
- `shrink_pullback`：跌 且量比 ≤0.8

对每个信号日，记录其后 **1/3/5 日收益**（用收盘价），并统计：
- 均值、胜率（>0 的比例）、最大回撤
- 与「全样本基准」对比（同区间所有交易日的后续收益）

**不做**：不预测、不调参、不构成交易建议；样本量小就明说。

    python -X utf8 tools/volume_backtest.py --days 60 --codes 300476.SZ,001228.SZ
    python -X utf8 tools/volume_backtest.py --days 120          # 默认取池内 CN
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))       # volume_profile 与本文件同目录

import volume_profile as vp  # noqa: E402

HORIZONS = (1, 3, 5)
SIGNAL_KINDS = ("breakout_with_volume", "shrink_pullback", "high_vol_no_gain",
                "oversold_drop5", "three_day_shrink", "break_low20", "gap_up")
BREAKOUT_MIN_RATIO = 1.5
BREAKOUT_MIN_PCT = 3.0
SHRINK_MAX_RATIO = 0.8


def _pool_cn() -> list[str]:
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8091/api/acad1/pool", timeout=20) as r:
            doc = json.loads(r.read().decode("utf-8"))
        return [str(x["code"]) for x in (doc.get("items") or [])
                if str(x.get("market") or "").upper() == "CN"]
    except Exception:  # noqa: BLE001
        return []


def load_series(code: str, days: int) -> list[dict]:
    """取升序日线（含 close/pct_chg/vol）。"""
    doc = vp._query("ts_daily", {"code": code, "limit": days})
    rows = vp._rows(doc)
    rows = sorted(rows, key=lambda r: str(r.get("trade_date")))
    out = []
    for r in rows:
        out.append({"date": str(r.get("trade_date")),
                    "close": vp._f(r.get("close")),
                    "low": vp._f(r.get("low")),
                    "pct": vp._f(r.get("pct_chg")),
                    "vol": vp._f(r.get("vol"))})
    return [x for x in out if x["close"] and x["vol"] is not None]


def scan(code: str, series: list[dict]) -> list[dict]:
    """逐日算多个候选信号 + 后续收益。前 20 日只作均量基准，不出信号。

    一次扫描同时评估多个判据——因为「哪个判据有区分度」本身就是要验证的问题，
    不能只测自己想到的那一个。
    """
    hits = []
    for i in range(20, len(series) - 1):
        prev = series[:i]
        vol = series[i]["vol"]
        ma20 = sum(x["vol"] for x in prev[-20:]) / 20 if len(prev) >= 20 else 0
        if not ma20:
            continue
        ratio = vol / ma20
        pct = series[i]["pct"] or 0.0
        high20 = max(x["close"] for x in prev[-20:])
        low20 = min(x["close"] for x in prev[-20:])
        close = series[i]["close"]
        prev_close = prev[-1]["close"]

        kinds = []
        # 1) 放量突破（已证伪，保留作对照）
        if pct > BREAKOUT_MIN_PCT and ratio >= BREAKOUT_MIN_RATIO and close > high20:
            kinds.append("breakout_with_volume")
        # 2) 缩量回调（历史上略低于基准，保留作对照）
        if pct < 0 and ratio <= SHRINK_MAX_RATIO:
            kinds.append("shrink_pullback")
        # 3) 放量滞涨：量比高但涨幅小——常见于出货
        if ratio >= 2.0 and 0 <= pct < 1.0:
            kinds.append("high_vol_no_gain")
        # 4) 超跌反弹：单日跌超 5%
        if pct <= -5.0:
            kinds.append("oversold_drop5")
        # 5) 连续三日缩量（当日及前两日量比均 < 0.8）
        if len(prev) >= 22:
            r2 = prev[-1]["vol"] / (sum(x["vol"] for x in prev[-21:-1]) / 20)
            r3 = prev[-2]["vol"] / (sum(x["vol"] for x in prev[-22:-2]) / 20)
            if ratio <= 0.8 and r2 <= 0.8 and r3 <= 0.8:
                kinds.append("three_day_shrink")
        # 6) 跌破 20 日低点（技术破位）
        if close < low20:
            kinds.append("break_low20")
        # 7) 跳空高开 >2%（当日最低 > 昨收）
        if (series[i].get("low") or 0) > prev_close * 1.02:
            kinds.append("gap_up")

        if not kinds:
            continue
        fwd = {}
        for h in HORIZONS:
            j = i + h
            fwd["r%d" % h] = (round((series[j]["close"] - close) / close * 100, 2)
                              if j < len(series) else None)
        for kind in kinds:
            hits.append({"code": code, "date": series[i]["date"], "signal": kind,
                         "pct": pct, "vol_ratio": round(ratio, 2), **fwd})
    return hits


def baseline(series: list[dict]) -> dict:
    """全样本基准：所有交易日（同样从第 20 日起）的后续收益均值与胜率。"""
    acc = {h: [] for h in HORIZONS}
    for i in range(20, len(series) - 1):
        close = series[i]["close"]
        for h in HORIZONS:
            j = i + h
            if j < len(series):
                acc[h].append((series[j]["close"] - close) / close * 100)
    return {("r%d" % h): {"n": len(v),
                          "mean": round(statistics.fmean(v), 2) if v else None,
                          "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None}
            for h, v in acc.items()}


def summarize(hits: list[dict], kind: str) -> dict:
    sub = [h for h in hits if h["signal"] == kind]
    out = {"signal": kind, "n": len(sub)}
    for h in HORIZONS:
        vals = [x["r%d" % h] for x in sub if x.get("r%d" % h) is not None]
        out["r%d" % h] = {
            "n": len(vals),
            "mean": round(statistics.fmean(vals), 2) if vals else None,
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1) if vals else None,
            "best": max(vals) if vals else None,
            "worst": min(vals) if vals else None,
        }
    return out


def _universe(n: int, seed: int = 20261116) -> list[str]:
    """从 ts_stock_basic 随机抽 n 只（排除北交所 92xxxx 与退市），可复现（固定 seed）。

    扩大样本的目的：12 只池内标的的回放存在选择偏差（它们本身是今天被选中的票）。
    """
    import random
    doc = vp._query("ts_stock_basic", {"limit": 6000})
    rows = vp._rows(doc)
    codes = []
    for r in rows:
        code = str(r.get("ts_code") or "")
        if not code or code.startswith("92") or code.startswith("8"):
            continue
        if str(r.get("list_status") or "L") != "L":
            continue
        codes.append(code)
    codes = sorted(set(codes))
    random.Random(seed).shuffle(codes)
    return codes[:n]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量能信号历史回放")
    ap.add_argument("--codes", default="")
    ap.add_argument("--universe", type=int, default=0,
                    help="改为从全市场随机抽 N 只（排除北交所/退市），用于扩大样本")
    ap.add_argument("--seed", type=int, default=20261116)
    ap.add_argument("--days", type=int, default=120, help="每只回看的交易日数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.universe:
        codes = _universe(args.universe, args.seed)
        print("UNIVERSE 随机抽样 %d 只（seed=%d）" % (len(codes), args.seed))
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()] or _pool_cn()
    if not codes:
        print("NO_CODES 池取不到，且未指定 --codes")
        return 2

    all_hits, base_acc = [], {h: [] for h in HORIZONS}
    for code in codes:
        try:
            series = load_series(code, args.days)
        except Exception as exc:  # noqa: BLE001
            print("%-11s ERR %s" % (code, type(exc).__name__))
            continue
        if len(series) < 25:
            print("%-11s SKIP 样本不足(%d)" % (code, len(series)))
            continue
        hits = scan(code, series)
        all_hits += hits
        b = baseline(series)
        for h in HORIZONS:
            if b["r%d" % h]["mean"] is not None:
                base_acc[h].append(b["r%d" % h]["mean"])
        print("%-11s bars=%-4d signals=%d (%s)" % (
            code, len(series), len(hits),
            ",".join("%s×%d" % (k, sum(1 for x in hits if x["signal"] == k))
                     for k in ("breakout_with_volume", "shrink_pullback"))))

    base = {("r%d" % h): {"mean_of_codes": round(statistics.fmean(v), 2) if v else None,
                          "codes": len(v)} for h, v in base_acc.items()}
    res = {"schema": "volume_backtest/v1", "codes": len(codes), "days": args.days,
           "thresholds": {"breakout_min_ratio": BREAKOUT_MIN_RATIO,
                          "breakout_min_pct": BREAKOUT_MIN_PCT,
                          "shrink_max_ratio": SHRINK_MAX_RATIO},
           "signals": [summarize(all_hits, k) for k in SIGNAL_KINDS],
           "baseline": base,
           "note": "回放仅验证信号区分度，不构成交易建议；样本量小时不做显著性判断"}

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print()
        print("%-22s %-4s %s" % ("signal", "n", "后续收益(均值/胜率) r1 / r3 / r5"))
        for s in res["signals"]:
            cells = []
            for h in HORIZONS:
                d = s["r%d" % h]
                cells.append("%s / %s%%" % (d["mean"], d["win_rate"]) if d["n"] else "—")
            print("%-22s %-4d %s" % (s["signal"], s["n"], " ｜ ".join(cells)))
        cells = []
        for h in HORIZONS:
            d = base["r%d" % h]
            cells.append("%s" % d["mean_of_codes"] if d["codes"] else "—")
        print("%-22s %-4s %s" % ("基准(全部交易日)", "-", " ｜ ".join(cells)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
