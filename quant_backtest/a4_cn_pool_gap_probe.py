# -*- coding: utf-8 -*-
"""CN 缠论池日期空档与批量异常只读探针。

只读：对照本地 TuShare trade_cal 快照（SSE is_open=1）量化 CN 池
detect_date 空档与单日批量分布，产出
quant_backtest/reports/a4_cn_pool_gap_report.json。结论边界：
- 覆盖比较基于本机 trade_cal 快照与本机池文件，不证明采集供给完备性；
- "空档"只表示该交易日无 detect_date 记录，不区分休市外原因
  （未运行/失败/当日无事件）；当日无事件与断供在此口径下不可区分；
- 大批量日只标记为"批量离群"，不判定为回填。
"""
import json
import statistics
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CAL_DIR = ROOT / "data" / "tushare" / "trade_cal"
POOL = ROOT / "data" / "chanlun_pool.jsonl"
REPORT = ROOT / "reports" / "a4_cn_pool_gap_report.json"

# 批量离群阈值：> 中位数 × 5 且 > 100 行才标记，避免小样本误报
OUTLIER_MED_RATIO = 5
OUTLIER_MIN_ROWS = 100


def load_calendar() -> list:
    """合并 trade_cal 重叠快照（判例：part 是全量快照非分片，必须去重）。"""
    frames = []
    for part in sorted(CAL_DIR.glob("part_*.parquet")):
        frames.append(pd.read_parquet(part))
    if not frames:
        return []
    cal = pd.concat(frames, ignore_index=True)
    cal = cal.drop_duplicates(subset=["cal_date"])
    cal = cal[(cal.get("is_open") == 1)]
    days = sorted(str(d) for d in cal["cal_date"].astype(str))
    return [d for d in days if len(d) == 8 and d.isdigit()]


def load_pool_dates() -> tuple:
    dates = Counter()
    bad = 0
    with POOL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                bad += 1
                continue
            day = obj.get("detect_date")
            if isinstance(day, str) and len(day) == 10:
                day = day.replace("-", "")
            if isinstance(day, str) and len(day) == 8 and day.isdigit():
                dates[day] += 1
            else:
                bad += 1
    return dates, bad


def main() -> dict:
    cal_days = load_calendar()
    pool_dates, bad_rows = load_pool_dates()
    if not cal_days or not pool_dates:
        report = {"status": "INSUFFICIENT_INPUT",
                  "cal_days": len(cal_days), "pool_dates": len(pool_dates)}
    else:
        # 窗口 = 池自身日期范围内的交易日（不是日历全区间）
        lo = min(pool_dates)
        hi = max(pool_dates)
        cal_set = set(cal_days)
        window = sorted(set(pool_dates)
                        | {d for d in cal_set if lo <= d <= hi})
        covered = {d for d in window if d in pool_dates}
        missing = [d for d in window if d not in pool_dates]
        counts = dict(pool_dates)
        med = statistics.median(counts.values())
        outliers = {d: n for d, n in sorted(counts.items())
                    if n > med * OUTLIER_MED_RATIO and n >= OUTLIER_MIN_ROWS}
        report = {
            "status": "OK",
            "scope": "LOCAL_OBSERVATION_NOT_SUPPLY_CERTIFICATION",
            "calendar_source": str(CAL_DIR),
            "window": [lo, hi],
            "trading_days_in_window": len(window),
            "pool_detect_dates": len(pool_dates),
            "covered_trading_days": len(covered),
            "missing_trading_days": len(missing),
            "missing_recent_10": missing[-10:],
            "missing_by_year": dict(Counter(d[:4] for d in missing)),
            "non_trading_dates_in_pool": sorted(
                d for d in pool_dates if d not in set(window)),
            "rows_per_date_median": med,
            "batch_outliers": outliers,
            "outlier_rule": "> median*%d and >= %d rows; NOT a backfill "
                            "verdict" % (OUTLIER_MED_RATIO, OUTLIER_MIN_ROWS),
            "bad_rows": bad_rows,
            "note": "missing date means no detect_date record; cannot "
                    "distinguish no-events vs not-run vs failed; calendar "
                    "is local snapshot",
        }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    if report.get("status") == "OK":
        print("window %s..%s trading=%d pool_dates=%d covered=%d missing=%d"
              % (lo, hi, len(window), len(pool_dates), len(covered),
                 len(missing)))
        print("missing_by_year:", report["missing_by_year"])
        print("missing last 10:", report["missing_recent_10"])
        print("batch_outliers:", report["batch_outliers"])
        print("non_trading_dates_in_pool:",
              report["non_trading_dates_in_pool"])
    else:
        print(report)
    print("report:", REPORT)
    return report


if __name__ == "__main__":
    main()
