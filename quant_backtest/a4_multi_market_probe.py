# -*- coding: utf-8 -*-
"""A4 多市场覆盖只读探针（US / HK），WO-QT-20261021-088 + 089。

只读：不写行情仓、不写池文件、不写 PG、不改扫描链、不改雪球库。
产出 quant_backtest/reports/a4_{market}_coverage_report.json，供
「必到市场清单」与多市场立项议题引用。

探针口径（对齐「股票数据词」四性框架，只观测不裁决）：
  1. 行情层 bar_store  —— parquet 文件数/行数分布/历史起点/末行新鲜度/
                          MIN_BARS(120) 达标率 ← 决定扫描链能否出信号
  2. 结构层 chanlun_pool —— 池文件存在性/行数/标的数/批次日期分布
  3. Regime 层          —— 大盘基准是否在盘（代码事实：US/HK 未接）
  4. 舆情层 xueqiu      —— norm_tickers.kind 分布（只读雪球库；WO-084 归情报）
  5. 策略适配层          —— 名称底册/涨跌停口径/后复权因子 三项可用性

结论边界（硬写进报告）：
  - 覆盖统计基于本机现有产物，不证明供给完备性或未来稳定性；
  - MIN_BARS 达标是「能扫」的必要条件，非「有信号」的充分条件；
  - 本探针不做任何市场是否「必到」的裁决，不出删源/加源建议。

运行：
  python a4_multi_market_probe.py --market US
  python a4_multi_market_probe.py --market HK
  python a4_multi_market_probe.py --all      # 两市场连跑
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"

# 扫描器结构门槛（与 chanlun_scan.MIN_BARS 同源；此处硬编码常量并在
# 运行期用 AST-free 方式核对，防探针与扫描器口径漂移）
MIN_BARS = 120
SCANNER = ROOT / "chanlun_scan.py"

POOLS = {
    "US": ROOT / "data" / "chanlun_pool_us.jsonl",
    "HK": ROOT / "data" / "chanlun_pool_hk.jsonl",
}
BAR_DIR = {"US": ROOT / "data" / "us", "HK": ROOT / "data" / "hk"}
XQ_DB = ROOT / "data" / "xueqiu" / "xueqiu_info.db"
ALL_CAP = 30000  # 行情层统计上限（超过则全量，仍逐文件读元数据）


def _log_run(event: str, detail: dict) -> None:
    """运行留痕（与 a4_market_coverage_probe 同款）：ok/error 各一行。"""
    try:
        log = REPORT_DIR / "a4_multi_market_snap" / "run_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "event": event,
                        **detail,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass  # 留痕失败不阻断主流程


def _scanner_min_bars() -> int | None:
    """从扫描器源码读 MIN_BARS（防口径漂移；读不到返回 None）。"""
    try:
        for line in SCANNER.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("MIN_BARS") and "=" in s:
                return int(s.split("=", 1)[1].strip().split()[0])
    except (OSError, ValueError):
        return None
    return None


# ---------- 1. 行情层：parquet 仓覆盖 ----------


def _rows_of(path: Path) -> int | None:
    """只读 parquet 行数（pyarrow 元数据优先，回退 pandas 全读）。"""
    try:
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(str(path)).metadata.num_rows)
    except Exception:
        pass
    try:
        import pandas as pd

        return int(len(pd.read_parquet(path)))
    except Exception:
        return None


def _span_of(path: Path) -> tuple[str, str] | None:
    """读首末交易日（只读两列，用于历史起点与新鲜度）。"""
    try:
        import pandas as pd

        d = pd.read_parquet(path, columns=["trade_date"])
        if d.empty:
            return None
        return (
            str(d["trade_date"].min()).replace("-", "")[:8],
            str(d["trade_date"].max()).replace("-", "")[:8],
        )
    except Exception:
        return None


def probe_bars(market: str) -> dict:
    d = BAR_DIR[market]
    if not d.is_dir():
        return {"status": "DIR_MISSING", "path": str(d)}
    files = sorted(f for f in d.iterdir() if f.suffix == ".parquet")
    if not files:
        return {"status": "NO_PARQUET", "path": str(d), "n_files": 0}

    sample = files if len(files) <= ALL_CAP else random.Random(0).sample(files, ALL_CAP)
    rows = []
    unreadable = []
    for f in sample:
        n = _rows_of(f)
        if n is None:
            unreadable.append(f.name)
        else:
            rows.append(n)

    if not rows:
        return {
            "status": "ALL_UNREADABLE",
            "n_files": len(files),
            "unreadable_sample": unreadable[:10],
        }

    rows_sorted = sorted(rows)
    n_pass = sum(1 for n in rows if n >= MIN_BARS)

    # 历史起点 / 末行新鲜度：抽 60 只读日期列（全读代价高，抽样有界标注）
    rnd = random.Random(20261021)
    span_samp = rnd.sample(sample, min(60, len(sample)))
    starts, ends = Counter(), Counter()
    for f in span_samp:
        sp = _span_of(f)
        if sp:
            starts[sp[0][:6]] += 1  # 月粒度（YYYYMM）
            ends[sp[1]] += 1  # 日粒度
    span_ok = sum(1 for f in span_samp if (_span_of(f) or ("", ""))[0])

    return {
        "status": "OK",
        "path": str(d),
        "n_files": len(files),
        "n_sampled": len(sample),
        "sample_is_full": len(sample) == len(files),
        "unreadable": len(unreadable),
        "rows_min": rows_sorted[0],
        "rows_p25": rows_sorted[len(rows_sorted) // 4],
        "rows_median": statistics.median(rows_sorted),
        "rows_p75": rows_sorted[3 * len(rows_sorted) // 4],
        "rows_max": rows_sorted[-1],
        "min_bars_gate": MIN_BARS,
        "n_pass_min_bars": n_pass,
        "pass_rate": round(n_pass / len(rows), 4),
        "start_month_hist": dict(sorted(starts.items())),
        "end_date_hist_top": dict(ends.most_common(8)),
        "span_sampled": span_ok,
        "note": "行数=该标的日线根数；历史起点按抽样的月粒度统计；末行日期=该标的最新交易日",
    }


# ---------- 2. 结构层：缠论池 ----------


def probe_pool(market: str) -> dict:
    p = POOLS[market]
    if not p.exists():
        return {
            "status": "FILE_MISSING",
            "path": str(p),
            "implication": "该市场尚无任何池产出（未跑过或跑过零产出，二者本探针不区分）",
        }
    rows = bad = undated = 0
    dates = Counter()
    codes: set[str] = set()
    types = Counter()
    try:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    bad += 1
                    continue
                if not isinstance(o, dict):
                    bad += 1
                    continue
                rows += 1
                day = o.get("detect_date")
                if isinstance(day, str) and day:
                    dates[day] += 1
                else:
                    undated += 1
                if o.get("code"):
                    codes.add(str(o["code"]))
                if o.get("type"):
                    types[str(o["type"])] += 1
    except OSError as e:
        return {"status": "READ_ERROR", "path": str(p), "error": str(e)[:200]}
    return {
        "status": "OK",
        "path": str(p),
        "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "rows": rows,
        "bad_json": bad,
        "undated_rows": undated,
        "unique_codes": len(codes),
        "n_dates": len(dates),
        "per_date_top": dict(dates.most_common(8)),
        "last_dates": sorted(dates)[-5:],
        "signal_types": dict(types.most_common(10)),
    }


# ---------- 3. Regime 层：大盘基准 ----------


def probe_regime(market: str) -> dict:
    """US/HK 大盘基准在 chanlun_scan 中显式未接（代码事实），此处复核数据面。"""
    idx = {"US": ["SPY", "QQQ", "DIA"], "HK": ["HSI", "2800", "00001"]}[market]
    d = BAR_DIR[market]
    found = {}
    for code in idx:
        f = d / ("%s.parquet" % code)
        if f.exists():
            sp = _span_of(f)
            found[code] = {
                "n": _rows_of(f),
                "from": sp[0] if sp else None,
                "to": sp[1] if sp else None,
            }
        else:
            found[code] = None
    return {
        "scanner_declared": "US/HK 大盘基准（SPY/恒生）未接 → []，reg=None（P8 口径不判不拦截）",
        "index_files_probed": found,
        "status": "NOT_CONNECTED",
        "implication": "无 Regime ⇒ 入口「今日多空」灯无法双源合成，只能亮单源或待判",
    }


# ---------- 4. 舆情层：雪球 norm_tickers.kind 分布（只读） ----------


def probe_xueqiu() -> dict:
    if not XQ_DB.exists():
        return {"status": "DB_MISSING", "path": str(XQ_DB)}
    try:
        con = sqlite3.connect(
            "file:%s?mode=ro" % XQ_DB.as_posix(), uri=True, timeout=20
        )
    except sqlite3.Error as e:
        return {"status": "DB_OPEN_ERROR", "error": str(e)[:200]}
    try:
        total = con.execute("select count(*) from xq_status_classified").fetchone()[0]
        rows = con.execute(
            "select norm_tickers from xq_status_classified "
            "where norm_tickers not in ('','[]')"
        ).fetchall()
        last_lab = con.execute(
            "select max(labeled_at) from xq_status_classified"
        ).fetchone()[0]
    except sqlite3.Error as e:
        con.close()
        return {"status": "QUERY_ERROR", "error": str(e)[:200]}
    finally:
        try:
            con.close()
        except Exception:
            pass

    kinds = Counter()
    uniq: dict[str, set] = {}
    shape = Counter()  # canonical 形态双轨观测
    for (nt,) in rows:
        try:
            arr = json.loads(nt)
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        for o in arr:
            if not isinstance(o, dict):
                continue
            k = str(o.get("kind") or "?")
            kinds[k] += 1
            can = o.get("canonical")
            if not can:
                continue
            can = str(can)
            uniq.setdefault(k, set()).add(can)
            if can[:2] in ("SH", "SZ", "BJ"):
                shape["prefix_%s" % can[:2]] += 1
            elif "." in can:
                shape["suffix_%s" % can.rsplit(".", 1)[-1]] += 1
            elif can.isdigit() and len(can) == 5:
                shape["hk_5digit"] += 1
            else:
                shape["other"] += 1

    return {
        "status": "OK",
        "db": str(XQ_DB),
        "classified_total": total,
        "rows_with_tickers": len(rows),
        "latest_labeled_at": last_lab,
        "kind_hits": dict(kinds.most_common()),
        "uniq_canonical_by_kind": {k: len(v) for k, v in sorted(uniq.items())},
        "canonical_shape": dict(shape.most_common()),
        "has_us_kind": any("us" in k for k in kinds),
        "note": (
            "norm_tickers 是对象数组 [{raw,kind,canonical}]，非字符串数组；"
            "下游消费必须按 kind 分流，按字符串直读会得零结果"
        ),
    }


# ---------- 5. 策略适配层：三项可用性 ----------


def probe_strategy_fitness(market: str) -> dict:
    return {
        "name_book": {"US/HK 名称底册未接": True, "effect": "池内只显示代码，无中文名"},
        "limit_rule": {
            "US/HK 无涨跌停停牌口径": True,
            "effect": "可成交默认 True，不判 mask",
        },
        "hfq_factor": {
            "仅 CN 有后复权因子源": True,
            "effect": "US/HK 不复权，长周期结构受除权跳空干扰",
        },
        "reusable": ["缠论买卖点（chanlun_scan --market %s 直跑）" % market],
        "not_reusable": [
            "青萍环境闸门（依赖 CN 指数口径）",
            "九维评分（CN 特征工程）",
            "共振筛选（依赖 CN 多策略同池）",
        ],
        "registry_status": "未在 portal/strategies.json 登记（无本市场记分卡）",
    }


# ---------- 主流程 ----------


def run(market: str) -> dict:
    scanner_gate = _scanner_min_bars()
    report = {
        "wo": "WO-QT-20261021-%s" % ("088" if market == "US" else "089"),
        "market": market,
        "scope": "LOCAL_ASSET_OBSERVATION_NOT_SUPPLY_CERTIFICATION",
        "verdict": "NO_MARKET_SCOPE_DECISION",
        "snapshot_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "min_bars_used": MIN_BARS,
        "min_bars_in_scanner": scanner_gate,
        "min_bars_drift": (scanner_gate is not None and scanner_gate != MIN_BARS),
        "layers": {
            "bar_store": probe_bars(market),
            "chanlun_pool": probe_pool(market),
            "regime": probe_regime(market),
            "sentiment_xueqiu": probe_xueqiu(),
            "strategy_fitness": probe_strategy_fitness(market),
        },
        "boundary": [
            "本地产物观测，不证明供给完备性/未来稳定性",
            "MIN_BARS 达标是「能扫」的必要条件，非「有信号」的充分条件",
            "本探针不做必到市场裁决、不出加源/删源建议",
            "雪球舆情归情报板块（WO-084），量化侧只读消费，不改库",
        ],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / ("a4_%s_coverage_report.json" % market.lower())
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    _log_run(
        "ok",
        {
            "market": market,
            "out": str(out),
            "n_files": report["layers"]["bar_store"].get("n_files"),
            "pass_rate": report["layers"]["bar_store"].get("pass_rate"),
        },
    )
    return report


def main():
    ap = argparse.ArgumentParser(
        description="A4 multi-market coverage probe (read-only)"
    )
    ap.add_argument("--market", choices=["US", "HK"], help="single market")
    ap.add_argument("--all", action="store_true", help="US + HK")
    args = ap.parse_args()
    if not args.market and not args.all:
        ap.error("需要 --market US|HK 或 --all")
    markets = ["US", "HK"] if args.all else [args.market]
    for m in markets:
        try:
            r = run(m)
        except Exception as e:  # closed on failure：记 error 后仍 raise
            _log_run("error", {"market": m, "err": "%s: %s" % (type(e).__name__, e)})
            raise
        bs = r["layers"]["bar_store"]
        pl = r["layers"]["chanlun_pool"]
        xq = r["layers"]["sentiment_xueqiu"]
        print(
            "[%s] bar_store=%s files=%s pass(min_bars)=%s rate=%s"
            % (
                m,
                bs.get("status"),
                bs.get("n_files"),
                bs.get("n_pass_min_bars"),
                bs.get("pass_rate"),
            )
        )
        print(
            "[%s] pool=%s rows=%s codes=%s dates=%s"
            % (
                m,
                pl.get("status"),
                pl.get("rows"),
                pl.get("unique_codes"),
                pl.get("n_dates"),
            )
        )
        print(
            "[%s] xueqiu kinds=%s has_us=%s"
            % (m, xq.get("kind_hits"), xq.get("has_us_kind"))
        )


if __name__ == "__main__":
    main()
