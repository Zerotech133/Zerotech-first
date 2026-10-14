# -*- coding: utf-8 -*-
"""量子双刀（double_knife）14:45 尾盘信号离线回测（工单 T-20261009-009 / W3-4）。

性质：独立研究脚本，只读产线文件与数据湖，不触碰 portal/qd_scanner.py、
positions.json 与任何生产数据表；不推送、不注册计划任务。

信号（严格复刻产线，零漂移）：
    直接 import agent_platform/engine/quant_double_knife.py 的
    quant_double_knife(profile="strict")——八重过滤 AND + Regime 四市况
    分层与 portal/qd_scanner.py scan_one 用的是同一份代码。全部指标为
    rolling/ewm 因果计算，天然只用 T 日及以前数据，无未来函数。

14:45 近似口径：
    产线 14:40 扫描 + 14:55 执行窗口，本回测用 T 日收盘价近似成交。
    湖内无分钟数据，无法直接量化 14:45→15:00 漂移；以「信号日收盘买
    vs 次日开盘买」的收益差分布侧面量化执行时点敏感性（见 JSON
    tail_gap 字段），方向性风险：动量型信号尾盘追价往往劣于收盘价，
    本回测结果对该偏差偏乐观 ~10-20bps/笔。

卖出语义：产线未定义（shadow 态）→ 跑持有 N=5/10/20 三档网格 +
    T+1 开盘卖出对照，全部列出，供 W3-5 卖出语义定义参考。

价格口径：
    信号 = 不复权湖价（照抄产线 scan_one 现行为）；
    收益 = 后复权价（ts_adj_factor 累积因子，最新湖锚根=1，chanlun
    W1-2 同款判例）——跨除权收益可比，避免假跳空污染。
    对照口径 --signal-price hfq 可看复权口径对信号质量的影响。

成本：CN 15bps 单边（买+卖 = 30bps round-trip），参照
    quant_backtest/backtest.py 的保守成本判例。

用法（唯一合法解释器）：
    "C:/Program Files/Python311/python.exe" -u quant_backtest/qd_backtest_1445.py \
        [--limit N] [--start 20231001] [--signal-price raw|hfq]
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))
sys.path.insert(0, str(ROOT / "quant_backtest"))

from quant_double_knife import PROFILE_CONFIG, quant_double_knife  # noqa: E402

ASHARE = ROOT / "quant_backtest" / "data" / "ashare"
REPORTS = HERE / "reports"


def result_json_path(signal_price):
    """结果文件按口径区分：raw=qd_backtest_1445_result.json（主口径，
    与 W3-4 基线同名同格式），hfq=qd_backtest_1445_result_hfq.json——
    避免 hfq 重跑覆写 raw 基线结果。"""
    if signal_price == "hfq":
        return REPORTS / "qd_backtest_1445_result_hfq.json"
    return REPORTS / "qd_backtest_1445_result.json"

# 指数文件剔除（湖内混入的指数 daily：000001.SH 上证、000688.SH 科创50、
# 399001.SZ 深成指、399006.SZ 创业板指）——与产线 scan_one 不同：产线全池
# 扫（指数也会被 6-200 价格带自然过滤大半），本回测显式剔除保证池纯净。
INDEX_FILES = {"000001_SH", "000688_SH", "399001_SZ", "399006_SZ"}

PROFILE = "strict"
COST_ROUNDTRIP = 0.0030          # 15bps 买 + 15bps 卖
HOLD_GRID = (5, 10, 20)          # 持有期网格（交易日）
COOLDOWN = 5                     # 首信号对照的冷却期（交易日）
DEFAULT_START = "20231001"       # 近 3 年信号窗口起点

REGIMES = ("trend_up", "rotation", "range", "trend_down")


# ── 后复权因子 ──────────────────────────────────────────────
_ADJ = None  # {ts_code: (dates ndarray 'YYYYMMDD', factors ndarray)}


def load_adj_factors():
    """湖 ts_adj_factor 全表懒加载（tdx_read 只读层，与 chanlun W1-2 同源）。

    归一判例照抄 chanlun_scan._hfq_scale：以每票全序列最后一根因子为
    基准（最新根=1），历史根等比缩放 → 最新根价格=不复权真实价。"""
    global _ADJ
    if _ADJ is not None:
        return _ADJ
    import tdx_read
    if not tdx_read.table_ready("ts_adj_factor"):
        print("[warn] ts_adj_factor 表未就绪 → 收益退回不复权口径（结果不可信，仅冒烟用）")
        _ADJ = {}
        return _ADJ
    df = tdx_read.read_range("ts_adj_factor", start="20230101",
                             columns=["ts_code", "trade_date", "adj_factor"])
    m = {}
    if df is not None and not df.empty:
        g_all = df.assign(
            _td=df["trade_date"].astype(str).str[:8],
            _fa=__import__("pandas").to_numeric(df["adj_factor"], errors="coerce"),
        ).dropna(subset=["_fa"])
        for code, g in g_all.groupby("ts_code", sort=False):
            td = g["_td"].to_numpy()
            fa = g["_fa"].to_numpy(dtype=float)
            order = td.argsort(kind="stable")
            m[code] = (td[order], fa[order])
    _ADJ = m
    print("[adj] 因子表加载：%d 票" % len(m))
    return _ADJ


def adj_series(code, dates):
    """dates（'YYYYMMDD' ndarray/Series）→ 归一因子 ndarray（末根=1）。

    缺失日用邻近值续（ffill/bfill，湖与因子表同源同步，缺口极少）；
    整票无因子/末根因子非正 → None（该票收益降级剔除）。"""
    import numpy as np
    fmap = load_adj_factors().get(code)
    if not fmap:
        return None
    ds, fs = fmap
    d = np.asarray([str(x)[:8] for x in dates])
    j = np.searchsorted(ds, d)
    j = np.clip(j, 0, len(ds) - 1)
    hit = ds[j] == d
    f = fs[j]
    if not hit.any():
        return None
    # 缺失日：前值续，头部缺失用首个有效值续
    last_valid = None
    for i in range(len(f)):
        if hit[i]:
            last_valid = f[i]
        else:
            f[i] = last_valid if last_valid is not None else f[i]
    if last_valid is None:
        nxt = f[hit][0]
        f[~hit] = nxt
    f = f / f[-1]
    return f if f[-1] > 0 else None


# ── 单票回测 ────────────────────────────────────────────────
def backtest_one(fp, start, signal_price, details=None):
    """单票：返回 (code, {hold_n: [net_ret...]}, {hold_n: [regime...]},
    first_only 计数, fallback 原因 or None, tail_gap 样本列表)。

    事件研究口径：每个信号日一笔独立交易；另计同票 COOLDOWN 冷却期内
    首信号（n_first）作拥挤度对照。exit 超湖尾的信号剔除并计数 n_open。
    details 传 list 时逐信号追加明细记录（date/regime/各持有期收益，
    超湖尾未平仓的持有期为 None），供 W3-5 变体离线过滤重算。"""
    import numpy as np
    import pandas as pd

    base = os.path.basename(fp)[:-8]
    code = base.replace("_", ".")
    out = {n: [] for n in HOLD_GRID}
    out_regime = {n: [] for n in HOLD_GRID}
    first_counts = {n: 0 for n in HOLD_GRID}
    tail_gap = []

    try:
        df = pd.read_parquet(fp, columns=["trade_date", "open", "high",
                                          "low", "close", "amount"])
    except Exception as e:
        return code, out, out_regime, first_counts, "read:%s" % e, tail_gap
    if len(df) < 100:  # 照抄产线 scan_one：warmup 不足跳过
        return code, out, out_regime, first_counts, "short", tail_gap
    df = df.rename(columns={"trade_date": "date"}).set_index("date")

    if signal_price == "hfq":
        f = adj_series(code, df.index.to_numpy())
        if f is None:
            return code, out, out_regime, first_counts, "no_adj", tail_gap
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].to_numpy(dtype=float) * f

    try:
        res = quant_double_knife(df, profile=PROFILE)
    except Exception as e:
        return code, out, out_regime, first_counts, "engine:%s" % e, tail_gap

    if signal_price != "hfq":
        f = adj_series(code, df.index.to_numpy())
        if f is None:
            return code, out, out_regime, first_counts, "no_adj", tail_gap

    open_adj = df["open"].to_numpy(dtype=float) * f
    close_adj = df["close"].to_numpy(dtype=float) * f
    n = len(df)

    sig_idx = np.flatnonzero(res["double_knife_signal"].to_numpy())
    dates = [str(x)[:8] for x in df.index]
    labels = res["regime_label"].to_numpy()
    start_i = int(np.searchsorted(np.array(dates), start))
    sig_idx = sig_idx[sig_idx >= start_i]
    if len(sig_idx) == 0:
        return code, out, out_regime, first_counts, None, tail_gap

    last_cooldown_end = -1
    for i in sig_idx:
        is_first = i > last_cooldown_end
        if is_first:
            last_cooldown_end = i + COOLDOWN - 1
        rec = None
        if details is not None:
            rec = {"code": code, "date": dates[i], "regime": str(labels[i])}
        for hold in HOLD_GRID:
            j = i + hold
            if j >= n:
                continue  # 未平仓（超湖锚）剔除
            gross = close_adj[j] / close_adj[i] - 1.0
            out[hold].append(gross - COST_ROUNDTRIP)
            out_regime[hold].append(str(labels[i]))
            if is_first:
                first_counts[hold] += 1
            if rec is not None:
                rec["r%d" % hold] = gross - COST_ROUNDTRIP
        if details is not None:
            details.append(rec)
        # T+1 开盘卖对照（持有1日极端对照：T 收盘买 → T+1 开盘卖）
        if i + 1 < n:
            gross = open_adj[i + 1] / close_adj[i] - 1.0
            out["t1_open"] = out.get("t1_open", [])
            out["t1_open"].append(gross - COST_ROUNDTRIP)
            out_regime.setdefault("t1_open", []).append(str(labels[i]))
        # 14:45 执行时点敏感性样本：信号日收盘买 → 次日开盘买 的差
        # （次日开盘买近似"错过尾盘窗口"的滞后成交成本分布）
        if i + 1 < n and len(tail_gap) < 400:
            tail_gap.append(open_adj[i + 1] / close_adj[i] - 1.0)

    return code, out, out_regime, first_counts, None, tail_gap


# ── 汇总统计 ────────────────────────────────────────────────
def stats(rets):
    import numpy as np
    a = np.asarray(rets, dtype=float)
    if len(a) == 0:
        return {"n": 0}
    gains, losses = a[a > 0], a[a < 0]
    pf = float(gains.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf") if len(gains) else 0.0
    # 事件序列口径回撤（按信号发生顺序的等权净值，非日度组合曲线）
    eq = np.cumprod(1.0 + a)
    dd = float(1.0 - (eq / np.maximum.accumulate(eq)).min()) if len(a) else 0.0
    return {
        "n": int(len(a)),
        "win_rate": round(float((a > 0).mean()), 4),
        "pf": round(pf, 4),
        "mean": round(float(a.mean()), 5),
        "median": round(float(np.median(a)), 5),
        "p5": round(float(np.percentile(a, 5)), 5),
        "p95": round(float(np.percentile(a, 95)), 5),
        "event_max_dd": round(dd, 4),
    }


def regime_breakdown(rets_by_regime):
    out = {}
    for hold, pairs in rets_by_regime.items():
        by = {}
        rets, regs = pairs
        for r, g in zip(regs, rets):
            by.setdefault(r, []).append(g)
        out[hold] = {k: stats(v) for k, v in sorted(by.items())}
    return out


# ── 主流程 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 只（冒烟）")
    ap.add_argument("--start", default=DEFAULT_START, help="信号统计起点 YYYYMMDD")
    ap.add_argument("--signal-price", choices=["raw", "hfq"], default="raw",
                    help="信号价格口径：raw=不复权（产线 scan_one 现行为），hfq=后复权")
    ap.add_argument("--details", default="",
                    help="逐信号明细导出路径（JSON 数组：code/date/regime/r5/r10/r20）"
                         "——W3-5 变体离线过滤重算用，不影响汇总结果")
    args = ap.parse_args()

    t0 = time.time()
    files = [fp for fp in sorted(glob.glob(str(ASHARE / "*.parquet")))
             if os.path.basename(fp)[:-8] not in INDEX_FILES]
    if args.limit:
        files = files[:args.limit]
    print("[run] 池 %d 只 | 窗口 %s~湖锚 | 信号口径 %s | 成本 %.0fbps RT"
          % (len(files), args.start, args.signal_price, COST_ROUNDTRIP * 1e4))

    load_adj_factors()

    agg = {n: [] for n in list(HOLD_GRID) + ["t1_open"]}
    agg_regime = {n: ([], []) for n in list(HOLD_GRID) + ["t1_open"]}
    first_total = {n: 0 for n in HOLD_GRID}
    fallback = {}
    gaps = []
    details = [] if args.details else None
    for k, fp in enumerate(files):
        code, out, out_regime, first_counts, fb, tail_gap = backtest_one(
            fp, args.start, args.signal_price, details=details)
        if fb:
            fallback[fb] = fallback.get(fb, 0) + 1
        for h, rets in out.items():
            agg[h].extend(rets)
            rr = agg_regime[h]
            rr[0].extend(rets)
            rr[1].extend(out_regime[h])
        for h, c in first_counts.items():
            first_total[h] += c
        gaps.extend(tail_gap)
        if (k + 1) % 500 == 0:
            print("[scan] %d/%d 已扫 %.0fs" % (k + 1, len(files), time.time() - t0), flush=True)

    results = {h: stats(v) for h, v in agg.items()}
    regime_detail = regime_breakdown(agg_regime)

    # 整体判据：PF≥1.1（用主口径 N=10 的 PF；三档全部列出）
    pf_main = results.get(10, {}).get("pf", 0.0)
    verdict = ("PF>=1.1 达标：够格起 60 日影子"
               if isinstance(pf_main, float) and pf_main >= 1.1
               else "PF<1.1 未达标：需调参后再测（不建议直接起影子）")

    import numpy as np
    gaps_a = np.asarray(gaps, dtype=float) if gaps else np.array([0.0])
    payload = {
        "task": "T-20261009-009 W3-4 量子双刀离线回测",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "profile": PROFILE,
            "eight_filters": ["cross_star_2d", "range_ok", "price_ok", "amount_ok",
                              "ma_stack_ok", "ma_distance_ok", "macd_ok", "regime_ok"],
            "blocked_regimes": sorted(PROFILE_CONFIG[PROFILE]["blocked_regimes"]),
            "start": args.start,
            "anchor": "20261013",
            "universe": "湖内全部 CN 股（剔除 4 只指数文件；ST/停牌无显式剔除——"
                        "照抄产线 scan_one 行为，仅 len>=100 warmup 过滤）",
            "entry": "T 日收盘（近似 14:45 信号确认 + 14:55 执行）",
            "sell_grid": ["hold_5", "hold_10", "hold_20", "t1_open_sell"],
            "cost_bps_roundtrip": 30,
            "cost_note": "15bps 买入 + 15bps 卖出（backtest.py CN 保守口径判例）",
            "signal_price": args.signal_price,
            "return_price": "hfq（ts_adj_factor 累积因子，末根=1，chanlun W1-2 判例）",
            "dedup": "事件研究：每信号一笔独立交易；另计 %d 交易日冷却首信号" % COOLDOWN,
        },
        "caliber_notes": {
            "1445_approx": "14:45 信号≈T 日 close 成交。湖无分钟数据，偏差以"
                           "「T 收盘 vs T+1 开盘」gap 分布侧面量化：均值 %.2f%%、"
                           "中位 %.2f%%、P5 %.2f%%、P95 %.2f%%（正=次日开盘更贵，"
                           "即错过尾盘窗口的滞后成本）。尾盘 15 分钟真实漂移未计，"
                           "动量信号追价通常使实盘劣于回测 ~10-20bps/笔。"
                           % (gaps_a.mean() * 100, float(np.median(gaps_a)) * 100,
                              float(np.percentile(gaps_a, 5)) * 100,
                              float(np.percentile(gaps_a, 95)) * 100),
            "sell_semantics": "产线未定义卖出语义（qd_scanner/qd_view 无 sell 分支）"
                              "→ 本回测以持有网格 N=5/10/20 + T+1 开盘卖对照补账，"
                              "卖出语义定义移交 W3-5 依本表决策。",
            "st_suspend": "ST 股（±5% 涨跌幅）未显式剔除，与产线一致；停牌日在湖中"
                          "无 bar 自然不产生信号。",
        },
        "overall": results,
        "first_signal_only": {"cooldown_days": COOLDOWN, "counts": first_total},
        "by_regime": regime_detail,
        "tail_gap": {"n": int(len(gaps)), "mean": round(float(gaps_a.mean()), 5),
                     "median": round(float(np.median(gaps_a)), 5),
                     "p5": round(float(np.percentile(gaps_a, 5)), 5),
                     "p95": round(float(np.percentile(gaps_a, 95)), 5)},
        "fallback": fallback,
        "verdict": verdict,
        "verdict_rule": "整体 PF>=1.1（主口径 hold_10）→ 够格起 60 日影子；否则调参后再测",
        "elapsed_s": round(time.time() - t0, 1),
        "disclaimer": "离线研究回测：结果不构成选股依据；影子验证前不下产线",
    }

    REPORTS.mkdir(exist_ok=True)
    result_json = result_json_path(args.signal_price)
    result_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    if details is not None:
        Path(args.details).write_text(
            json.dumps({"signal_price": args.signal_price, "start": args.start,
                        "n": len(details), "records": details},
                       ensure_ascii=False), encoding="utf-8")
        print("[details] %d 条逐信号明细 → %s" % (len(details), args.details))
    print("[done] %.0fs → %s" % (time.time() - t0, result_json))
    for h in list(HOLD_GRID) + ["t1_open"]:
        s = results.get(h, {})
        print("[res] %-8s n=%-6d win=%.2f%% PF=%.3f mean=%.3f%%"
              % (h, s.get("n", 0), s.get("win_rate", 0) * 100,
                 s.get("pf", 0), s.get("mean", 0) * 100))
    print("[verdict]", verdict)


if __name__ == "__main__":
    main()
