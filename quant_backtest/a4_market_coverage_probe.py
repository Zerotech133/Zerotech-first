# -*- coding: utf-8 -*-
"""A4 必到市场覆盖只读探针：统计三市场缠论池文件的历史批次覆盖。

只读：不写池文件、不写 PG、不改生产扫描链。产出
quant_backtest/reports/a4_market_coverage_report.json，供
"必到市场清单"议题拍板引用。结论边界：
- 覆盖统计基于本机现有池文件，不证明采集供给的完备性或未来稳定性；
- 缺失日期不区分"非交易日/未采集/采集失败"，日历归属未拍板；
- 本探针不做任何市场是否"必到"的裁决。
"""

import json
import statistics
from collections import Counter
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"

POOLS = {
    "CN": ROOT / "data" / "chanlun_pool.jsonl",
    "US": ROOT / "data" / "chanlun_pool_us.jsonl",
    "HK": ROOT / "data" / "chanlun_pool_hk.jsonl",
}

# 运行留痕：automation 会话 stdout 不落盘，失败无法事后审计（如 2026-10-19
# 16:40 缺档无法判别"未触发"还是"执行失败"）。ok/error 各记一行，error 仍 raise。
RUN_LOG = REPORT_DIR / "a4_market_coverage_snap" / "run_log.jsonl"


def _log_run(event: str, detail: dict) -> None:
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            **detail,
        }
        with RUN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/a4_market_coverage_probe.py", exc)


def scan_pool(path: Path) -> dict:
    if not path.exists():
        return {"status": "FILE_MISSING", "path": str(path)}
    rows = 0
    bad_json = 0
    empty_lines = 0
    dates = Counter()
    codes = set()
    undated = 0
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    empty_lines += 1
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    bad_json += 1
                    continue
                if not isinstance(obj, dict):
                    bad_json += 1
                    continue
                rows += 1
                day = obj.get("detect_date")
                if isinstance(day, str) and day:
                    dates[day] += 1
                else:
                    undated += 1
                codes.add(obj.get("code"))
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "UNREADABLE", "path": str(path), "reason": type(exc).__name__}

    if rows == 0 or not dates:
        # 全部行缺 detect_date 时 dates 为空，min/max 会崩；降级为无统计
        per_date = {}
        stats = None
    else:
        per_date = {d: dates[d] for d in sorted(dates)}
        counts = list(dates.values())
        stats = {
            "distinct_detect_dates": len(dates),
            "date_range": [min(dates), max(dates)] if dates else None,
            "rows_per_date_min": min(counts),
            "rows_per_date_median": statistics.median(counts),
            "rows_per_date_max": max(counts),
            "rows_per_date_mean": round(statistics.mean(counts), 1),
        }
    return {
        "status": "OK",
        "path": str(path),
        "rows": rows,
        "bad_json": bad_json,
        "empty_lines": empty_lines,
        "undated_rows": undated,
        "unique_codes": len(codes),
        "per_date_counts": per_date,
        "stats": stats,
        "note": (
            "pool-file observation only; NOT a completeness or "
            "stability proof; missing dates are not classified "
            "(holiday vs not-collected vs failed)"
        ),
    }


def main() -> dict:
    from datetime import datetime

    try:
        markets = {code: scan_pool(path) for code, path in POOLS.items()}
        snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 每市场仅留最新 3 个 detect_date 的行数，控制快照体积
        brief = {}
        for code, data in markets.items():
            if data.get("status") != "OK":
                brief[code] = {"status": data.get("status")}
                continue
            tail = dict(list(data["per_date_counts"].items())[-3:])
            brief[code] = {
                "status": "OK",
                "rows": data["rows"],
                "unique_codes": data["unique_codes"],
                "stats": data["stats"],
                "latest_dates": tail,
            }
        report = {
            "scope": "LOCAL_POOL_FILE_OBSERVATION_NOT_SUPPLY_CERTIFICATION",
            "verdict": "NO_MARKET_SCOPE_DECISION",
            "snapshot_at": snapshot_at,
            "markets": markets,
        }
        REPORT_DIR.mkdir(exist_ok=True)
        out = REPORT_DIR / "a4_market_coverage_report.json"
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # dated 快照：支撑"必到市场"议题的连续观测（拍板须连续≥1个月）
        snap_dir = REPORT_DIR / "a4_market_coverage_snap"
        snap_dir.mkdir(exist_ok=True)
        snap = snap_dir / ("%s.json" % datetime.now().strftime("%Y%m%d_%H%M"))
        snap.write_text(
            json.dumps(
                {
                    "scope": report["scope"],
                    "snapshot_at": snapshot_at,
                    "markets": brief,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _log_run(
            "ok",
            {
                "snapshot": snap.name,
                "markets": {code: data.get("status") for code, data in markets.items()},
            },
        )
        for code, data in markets.items():
            if data["status"] != "OK":
                print(code, data["status"])
                continue
            s = data["stats"]
            print(
                "%s rows=%d codes=%d days=%d range=%s..%s "
                "rows/date min=%s median=%s max=%s mean=%s bad=%d"
                % (
                    code,
                    data["rows"],
                    data["unique_codes"],
                    s["distinct_detect_dates"],
                    s["date_range"][0],
                    s["date_range"][1],
                    s["rows_per_date_min"],
                    s["rows_per_date_median"],
                    s["rows_per_date_max"],
                    s["rows_per_date_mean"],
                    data["bad_json"],
                )
            )
        print("report:", out)
        print("snapshot:", snap)
        return report
    except Exception as exc:
        _log_run("error", {"error_type": type(exc).__name__, "error": str(exc)})
        raise


if __name__ == "__main__":
    main()
