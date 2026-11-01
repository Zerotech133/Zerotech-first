# -*- coding: utf-8 -*-
"""只读事件研究 harness v1（2026-11-01 WO-STRAT-EVENTSTUDY-20261101 / task-29）。

面向 H37/H38/H39 研究假设（`docs/plans/2026-11-01_H37_H39策略假设_v1.md`，
该文档在 WO-STRAT-HYP worktree）的**只读**事件研究工具：

输入（离线 CSV/JSON 或 DataFrame）：
  events       事件表：date, anchor_symbol, event_id
  prices       日线面板：date, symbol, close（研究用，调用方应使用复权价）
  benchmark    基准（可选）：
               * None → 全市场等权（prices 面板内全部标的日收益等权均值，
                 与 `tools/research/event_study.py` 市场调整法同口径）；
               * 列 date,return（小数）→ 直接用（最 PIT 友好）；
               * 列 date,close → 按 close 计算日收益。

输出（每事件 + 汇总）：
  * CAR[0,+N]：默认 20 个交易日（T0..T0+N 含端点，共 N+1 个日度 AR 求和），
    可 `--horizons 5,20,60` 多窗口；
  * 每事件 CAR、路径（最大窗口）、最大回撤/达峰日、状态（ok/anchor_missing/
    no_prior_close/insufficient_window/data_gaps/event_after_data）；
  * 事件级汇总（按 event_id 聚类后）：n_events/n_rows/mean/median/std/t/win_rate；
    `n_events < min_events（默认 30）` → `insufficient_sample=true`、
    `verdict=insufficient`（不出结论，不伪造显著）；
  * 重叠提示：同 anchor 或同 event_id 且 T0 距离 ≤ 最大窗口的事件对/聚类，
    提示需非重叠抽样/block bootstrap（`tools/research/stat_utils.py`）。

PIT 纪律：
  * T0 = 事件日（非交易日顺延到之后第一个交易日）；只用 T0 之前一根收盘价
    作为事件日收益基准，其余只用 T0..T0+N 的收盘价；窗口后数据一律不使用；
  * 事件日之后数据不足 N 日 → 该窗口 CAR=None（事件标记 insufficient_window），
    不用未来数据补齐、不外推；
  * 不联网、不写生产数据、不产买卖信号、不接生产选股/注册表。

CLI：
  python tools/research/hypothesis_event_study.py --events events.csv \
      --prices daily.csv [--benchmark bench.csv] [--horizons 5,20,60] --json
  （退出码：0=出报告；3=数据/用法错误，fail-visible）
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "hypothesis_event_study/v1"
EVENT_COLUMNS = ("date", "anchor_symbol", "event_id")
PRICE_COLUMNS = ("date", "symbol", "close")
DEFAULT_HORIZONS = (20,)
DEFAULT_MIN_EVENTS = 30
MAX_HORIZON = 250
MAX_BAD_EXAMPLES = 20
MAX_OVERLAP_PAIRS = 200


class EventStudyError(ValueError):
    """结构性数据错误（缺列/空表/无有效行/参数非法）——fail-visible。"""


# ---------------------------------------------------------------- 读入/清洗
def _read_table(source, kind: str) -> pd.DataFrame:
    """source 可为 DataFrame 或 CSV/JSON 路径。"""
    if isinstance(source, pd.DataFrame):
        return source.copy()
    path = Path(source)
    if not path.is_file():
        raise EventStudyError("%s 文件不存在：%s" % (kind, path))
    suffix = path.suffix.lower()
    try:
        if suffix in (".csv", ".txt"):
            return pd.read_csv(path)
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("rows") or data.get("data") or data.get(kind)
            if not isinstance(data, list) or not data:
                raise EventStudyError("%s JSON 必须是记录数组且非空：%s" % (kind, path))
            return pd.DataFrame(data)
        raise EventStudyError("%s 不支持扩展名 %r（仅 .csv/.json）" % (kind, suffix))
    except EventStudyError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise EventStudyError("%s 读取失败：%s: %s"
                             % (kind, type(exc).__name__, str(exc)[:200]))


def _norm_dates(series: pd.Series) -> pd.Series:
    try:
        dt = pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.strftime("%Y-%m-%d")


def _clean_events(df: pd.DataFrame) -> tuple:
    missing = [c for c in EVENT_COLUMNS if c not in df.columns]
    if missing:
        raise EventStudyError("事件表缺列：%s（必需 %s）"
                             % (", ".join(missing), ", ".join(EVENT_COLUMNS)))
    if df.empty:
        raise EventStudyError("事件表为空")
    work = df.copy()
    work["_date"] = _norm_dates(work["date"])
    work["_symbol"] = work["anchor_symbol"].astype("string").str.strip()
    work["_event_id"] = work["event_id"].astype("string").str.strip()
    bad = (work["_date"].isna() | work["_symbol"].isna()
           | work["_symbol"].eq("") | work["_event_id"].isna()
           | work["_event_id"].eq(""))
    bad_count = int(bad.sum())
    examples = []
    for idx in work.index[bad][:MAX_BAD_EXAMPLES]:
        reasons = []
        if pd.isna(work.at[idx, "_date"]):
            reasons.append("date 不可解析")
        if pd.isna(work.at[idx, "_symbol"]) or work.at[idx, "_symbol"] == "":
            reasons.append("anchor_symbol 为空")
        if pd.isna(work.at[idx, "_event_id"]) or work.at[idx, "_event_id"] == "":
            reasons.append("event_id 为空")
        examples.append({"row": int(idx) if isinstance(idx, (int, np.integer))
                         else str(idx), "reason": "; ".join(reasons)})
    work = work[~bad].copy()
    if work.empty:
        raise EventStudyError("事件表无有效行（坏行 %d）" % bad_count)
    dup = int(len(work) - len(work.drop_duplicates(
        subset=["_date", "_symbol", "_event_id"])))
    work = work.drop_duplicates(subset=["_date", "_symbol", "_event_id"],
                                keep="first")
    work = work.reset_index(drop=True)
    warnings = []
    if bad_count:
        warnings.append("事件表坏行 %d 行已剔除" % bad_count)
    if dup:
        warnings.append("事件表重复 (date,anchor_symbol,event_id) %d 行去重" % dup)
    return work, {"bad_rows": bad_count, "bad_examples": examples,
                  "duplicate_rows": dup, "warnings": warnings}


def _clean_prices(df: pd.DataFrame) -> tuple:
    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise EventStudyError("行情面板缺列：%s（必需 %s）"
                             % (", ".join(missing), ", ".join(PRICE_COLUMNS)))
    if df.empty:
        raise EventStudyError("行情面板为空")
    work = df.copy()
    work["_date"] = _norm_dates(work["date"])
    work["_symbol"] = work["symbol"].astype("string").str.strip()
    work["_close"] = pd.to_numeric(work["close"], errors="coerce")
    work.loc[~np.isfinite(work["_close"]), "_close"] = np.nan
    bad = (work["_date"].isna() | work["_symbol"].isna()
           | work["_symbol"].eq("") | work["_close"].isna()
           | (work["_close"] <= 0))
    bad_count = int(bad.sum())
    work = work[~bad].copy()
    if work.empty:
        raise EventStudyError("行情面板无有效行（坏行 %d）" % bad_count)
    dup = int(len(work) - len(work.drop_duplicates(subset=["_date", "_symbol"])))
    work = work.drop_duplicates(subset=["_date", "_symbol"], keep="last")
    work = work.sort_values(["_date", "_symbol"]).reset_index(drop=True)
    warnings = []
    if bad_count:
        warnings.append("行情面板坏行 %d 行已剔除" % bad_count)
    if dup:
        warnings.append("行情面板重复 (date,symbol) %d 行，保留最后一条" % dup)
    out = work[["_date", "_symbol", "_close"]].rename(
        columns={"_date": "date", "_symbol": "symbol", "_close": "close"})
    return out, {"bad_rows": bad_count, "duplicate_rows": dup,
                 "warnings": warnings}


# ---------------------------------------------------------------- 基准
def _benchmark_returns(benchmark, dates: list, rets: pd.DataFrame) -> tuple:
    """→ (Series indexed by date, label)。None=全市场等权。"""
    if benchmark is None or (isinstance(benchmark, pd.DataFrame) and benchmark.empty):
        series = rets.mean(axis=1)
        return series, "equal_weight_all_symbols"
    df = benchmark.copy()
    if "date" not in df.columns:
        raise EventStudyError("基准缺列：date")
    df["_date"] = _norm_dates(df["date"])
    if "return" in df.columns:
        vals = pd.to_numeric(df["return"], errors="coerce")
        label = "benchmark_return_column"
    elif "close" in df.columns:
        tmp = df[["_date"]].copy()
        tmp["_close"] = pd.to_numeric(df["close"], errors="coerce")
        tmp = (tmp.dropna(subset=["_date", "_close"])
                  .drop_duplicates(subset=["_date"], keep="last")
                  .sort_values("_date"))
        vals = tmp["_close"].pct_change()
        df = tmp
        label = "benchmark_close_column"
    else:
        raise EventStudyError("基准需含列 return 或 close（另需 date）")
    df = df.dropna(subset=["_date"]).copy()
    df["_ret"] = pd.to_numeric(vals, errors="coerce").to_numpy()
    series = (df.drop_duplicates(subset=["_date"], keep="last")
                .set_index("_date")["_ret"])
    series = series.reindex(dates)
    return series, label


# ---------------------------------------------------------------- 统计
def _stats(values) -> dict:
    vals = np.asarray([v for v in values if v is not None and np.isfinite(v)],
                      dtype=float)
    n = int(len(vals))
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "t": None, "win_rate": None, "min": None, "max": None}
    mean = float(np.mean(vals))
    std = float(np.std(vals, ddof=1)) if n >= 2 else None
    t_stat = (mean / (std / float(np.sqrt(n)))
              if (std is not None and std > 0) else None)
    return {
        "n": n,
        "mean": round(mean, 6),
        "median": round(float(np.median(vals)), 6),
        "std": round(std, 6) if std is not None else None,
        "t": round(t_stat, 4) if t_stat is not None else None,
        "win_rate": round(float(np.mean(vals > 0)), 4),
        "min": round(float(np.min(vals)), 6),
        "max": round(float(np.max(vals)), 6),
    }


# ---------------------------------------------------------------- 主计算
def study(events, prices, benchmark=None, *, horizons=(20,),
          min_events: int = DEFAULT_MIN_EVENTS) -> dict:
    """事件研究主入口 → 报告 dict（JSON 友好）。数据错误抛 EventStudyError。"""
    horizons = sorted({int(h) for h in horizons})
    if not horizons or any(h < 1 or h > MAX_HORIZON for h in horizons):
        raise EventStudyError("horizons 必须为 1..%d 的交易日数（收到 %r）"
                             % (MAX_HORIZON, list(horizons)))
    if int(min_events) < 1:
        raise EventStudyError("min_events 必须 ≥1")
    events_df, event_meta = _clean_events(_read_table(events, "events"))
    prices_df, price_meta = _clean_prices(_read_table(prices, "prices"))
    warnings = list(event_meta["warnings"]) + list(price_meta["warnings"])

    close = prices_df.pivot(index="date", columns="symbol", values="close")
    close = close.sort_index()
    dates = list(close.index)
    rets = close.pct_change()
    bench, bench_label = _benchmark_returns(
        _read_table(benchmark, "benchmark") if benchmark is not None else None,
        dates, rets)
    symbols = set(close.columns)

    max_h = max(horizons)
    counts = {"events_rows": int(len(events_df)),
              "prices_rows": int(len(prices_df)),
              "trading_days": len(dates), "symbols": len(symbols)}
    per_event = []
    for _, ev in events_df.iterrows():
        event_id = str(ev["_event_id"])
        symbol = str(ev["_symbol"])
        ev_date = str(ev["_date"])
        item = {"event_id": event_id, "date": ev_date, "anchor_symbol": symbol,
                "t0": None, "t0_shifted": False, "status": "ok",
                "cars": {str(h): None for h in horizons},
                "missing_horizons": [], "path": None, "max_drawdown": None,
                "peak_day": None, "overlaps_with": []}
        idx = bisect.bisect_left(dates, ev_date)
        if idx >= len(dates):
            item["status"] = "event_after_data"
            per_event.append(item)
            continue
        item["t0"] = dates[idx]
        item["t0_shifted"] = dates[idx] != ev_date
        if symbol not in symbols:
            item["status"] = "anchor_missing"
            per_event.append(item)
            continue
        if idx < 1:
            item["status"] = "no_prior_close"
            per_event.append(item)
            continue
        close_col = close[symbol]
        bench_col = bench
        valid_any = False
        best_path = None
        for h in horizons:
            j = idx + h
            if j >= len(dates):
                item["missing_horizons"].append(h)
                continue
            ars = []
            ok = True
            for t in range(idx, j + 1):
                a_prev = close_col.iloc[t - 1]
                a_now = close_col.iloc[t]
                b = bench_col.iloc[t]
                if not (np.isfinite(a_prev) and np.isfinite(a_now)
                        and np.isfinite(b)) or a_prev <= 0:
                    ok = False
                    break
                ars.append(float(a_now / a_prev - 1.0 - b))
            if not ok:
                item["missing_horizons"].append(h)
                continue
            car = float(sum(ars))
            item["cars"][str(h)] = round(car, 6)
            valid_any = True
            if best_path is None or h == max_h:
                cum = np.cumsum(ars)
                best_path = [round(float(x), 6) for x in cum]
                item["max_drawdown"] = round(
                    float(np.min(cum - np.maximum.accumulate(cum))), 6)
                item["peak_day"] = int(np.argmax(cum)) + 1
        if not valid_any:
            item["status"] = ("insufficient_window"
                              if item["missing_horizons"] else "data_gaps")
        item["path"] = best_path
        per_event.append(item)

    valid = [e for e in per_event if e["status"] == "ok"]
    excluded = [e for e in per_event if e["status"] != "ok"]

    # 事件聚类：同一 event_id 多 anchor → 先按 event_id 取均值再统计
    by_event_id = []
    groups: dict = {}
    for e in valid:
        groups.setdefault(e["event_id"], []).append(e)
    for event_id, rows in groups.items():
        entry = {"event_id": event_id,
                 "anchors": sorted({r["anchor_symbol"] for r in rows}),
                 "n_rows": len(rows),
                 "dates": sorted({r["t0"] for r in rows}),
                 "cars": {}}
        for h in horizons:
            vals = [r["cars"][str(h)] for r in rows
                    if r["cars"][str(h)] is not None]
            entry["cars"][str(h)] = round(float(np.mean(vals)), 6) if vals else None
        by_event_id.append(entry)

    stats = {}
    for h in horizons:
        event_vals = [x["cars"][str(h)] for x in by_event_id
                      if x["cars"][str(h)] is not None]
        row_vals = [r["cars"][str(h)] for r in valid
                    if r["cars"][str(h)] is not None]
        s = _stats(event_vals)
        insufficient = s["n"] < int(min_events)
        stats[str(h)] = {
            **s,
            "insufficient_sample": bool(insufficient),
            "verdict": "insufficient" if insufficient else "observation",
            "min_events": int(min_events),
            "row_stats": _stats(row_vals),
            "row_count": len(row_vals),
            "excluded_events": len(per_event) - len(valid),
        }
        if insufficient:
            warnings.append("horizon=%s 事件级有效样本 n=%s < min_events=%s"
                            "——样本不足，不出结论（仅观察支持上限）"
                            % (h, s["n"], int(min_events)))

    # 重叠提示：不同事件且 T0 距离 ≤ 最大窗口（观察窗重叠→样本不独立）
    pairs = []
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            a, b = valid[i], valid[j]
            if a["event_id"] == b["event_id"]:
                continue    # 同事件多标的归入 event_clusters（统计已按 event 聚类）
            gap = abs(dates.index(a["t0"]) - dates.index(b["t0"]))
            if gap <= max_h:
                pairs.append({"a": a["event_id"], "b": b["event_id"],
                              "anchors": [a["anchor_symbol"], b["anchor_symbol"]],
                              "t0_gap_days": int(gap),
                              "same_anchor": a["anchor_symbol"] == b["anchor_symbol"]})
    pair_map: dict = {}
    for p in pairs:
        pair_map.setdefault((p["a"], p["b"]), []).append(p["t0_gap_days"])
    for e in valid:
        others = set()
        for (a, b) in pair_map:
            if e["event_id"] == a:
                others.add(b)
            elif e["event_id"] == b:
                others.add(a)
        e["overlaps_with"] = sorted(others)
    clusters = []
    for event_id, rows in groups.items():
        if len({r["anchor_symbol"] for r in rows}) > 1:
            clusters.append({"event_id": event_id,
                             "anchors": sorted({r["anchor_symbol"] for r in rows}),
                             "note": "同一事件多标的：统计已按 event_id 聚类取均值"})
    overlap_report = {
        "pair_count": len(pairs),
        "pairs": pairs[:MAX_OVERLAP_PAIRS],
        "event_clusters": clusters,
        "note": ("重叠事件/同事件多标的：统计已按 event_id 聚类；"
                 "进一步显著性请用非重叠抽样+block bootstrap"
                 "（tools/research/stat_utils.py）；n<30 不出结论。"),
    }
    if pairs:
        warnings.append("检测到 %d 对重叠事件（同 anchor/同 event 且窗口重叠）"
                        "——需非重叠抽样/block bootstrap 修正" % len(pairs))
    status_counts: dict = {}
    for e in per_event:
        status_counts[e["status"]] = status_counts.get(e["status"], 0) + 1

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "params": {"horizons": list(horizons), "min_events": int(min_events),
                   "car_definition": "CAR[0,+N]=sum_{t=T0..T0+N}(anchor_ret_t - bench_ret_t)，"
                                     "含 T0 当日（用 T0-1 收盘计算当日收益）",
                   "pit": "T0=事件日或之后第一个交易日；窗口后数据不使用；"
                          "T0-1 收盘仅用于事件日收益基准"},
        "inputs": {**counts, "benchmark": bench_label,
                   "event_bad_rows": event_meta["bad_rows"],
                   "price_bad_rows": price_meta["bad_rows"]},
        "events": per_event,
        "by_event_id": by_event_id,
        "stats": stats,
        "overlaps": overlap_report,
        "excluded": {"counts": status_counts,
                     "examples": [{"event_id": e["event_id"],
                                   "date": e["date"],
                                   "anchor_symbol": e["anchor_symbol"],
                                   "status": e["status"],
                                   "missing_horizons": e["missing_horizons"]}
                                  for e in excluded[:MAX_BAD_EXAMPLES]]},
        "event_bad_rows": event_meta["bad_examples"],
        "warnings": warnings,
        "notes": [
            "只读研究工具：不产买卖信号、不接生产选股/策略注册、不写生产数据。",
            "T0=事件日或之后第一个交易日；PIT：窗口后数据不使用，T0-1 收盘仅用于事件日收益。",
            "H37-H39 预注册口径：CAR[0,20] 主判据；n<30 一律 insufficient（观察支持上限）；"
            "重叠样本须非重叠抽样/block bootstrap。",
            "基准默认全市场等权（与 event_study.py 市场调整法同口径）；"
            "也可传 date,return 或 date,close 基准文件。",
        ],
    }


# 语义别名
run = study


# ---------------------------------------------------------------- CLI
def _print_report(report: dict) -> None:
    print("== 事件研究（只读）%s ==" % report["schema"])
    inp = report["inputs"]
    print("events=%s prices=%s days=%s symbols=%s benchmark=%s"
          % (inp["events_rows"], inp["prices_rows"], inp["trading_days"],
             inp["symbols"], inp["benchmark"]))
    ex = report["excluded"]["counts"]
    print("excluded=%s" % json.dumps(ex, ensure_ascii=False))
    for h, s in report["stats"].items():
        print("CAR[0,+%s]  n_events=%s n_rows=%s mean=%s median=%s t=%s 胜率=%s "
              "insufficient=%s"
              % (h, s["n"], s["row_count"], s["mean"], s["median"], s["t"],
                 s["win_rate"], s["insufficient_sample"]))
    ov = report["overlaps"]
    print("overlaps: pairs=%s clusters=%s"
          % (ov["pair_count"], len(ov["event_clusters"])))
    for w in report["warnings"]:
        print("[warn] %s" % w)
    for note in report["notes"]:
        print("[note] %s" % note)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="只读事件研究 harness（CAR[0,+N]、多事件/多标的、重叠提示；离线）")
    ap.add_argument("--events", required=True, help="事件表 CSV/JSON（date,anchor_symbol,event_id）")
    ap.add_argument("--prices", required=True, help="日线面板 CSV/JSON（date,symbol,close）")
    ap.add_argument("--benchmark", default="",
                    help="基准 CSV/JSON（date,return 或 date,close）；缺省=全市场等权")
    ap.add_argument("--horizons", default="20",
                    help="CAR 窗口（交易日数，逗号分隔），默认 20")
    ap.add_argument("--min-events", type=int, default=DEFAULT_MIN_EVENTS)
    ap.add_argument("--json", action="store_true", help="打印 JSON 报告")
    ap.add_argument("--out", default="", help="报告 JSON 落盘路径")
    args = ap.parse_args(argv)
    try:
        hs = sorted({int(x) for x in str(args.horizons).replace(";", ",").split(",")
                     if x.strip()})
        report = study(args.events, args.prices,
                       benchmark=args.benchmark or None,
                       horizons=hs or DEFAULT_HORIZONS,
                       min_events=args.min_events)
    except (EventStudyError, ValueError) as exc:
        print("[ERROR] %s" % exc, file=sys.stderr)
        return 3
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            if not args.json:
                print("[report] -> %s" % out)
        except OSError as exc:
            print("[ERROR] 报告落盘失败：%s" % str(exc)[:200], file=sys.stderr)
            return 3
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
