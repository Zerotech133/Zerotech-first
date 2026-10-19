# -*- coding: utf-8 -*-
"""生成历史回补计划，日期来自 TuShare trade_cal 本地只读快照。"""
from pathlib import Path
import json
import pandas as pd


def main() -> None:
    root = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare\trade_cal")
    frames = []
    for path in sorted(root.glob("*.parquet")):
        frame = pd.read_parquet(path, columns=["exchange", "cal_date", "is_open"])
        frames.append(frame[(frame["exchange"].astype(str) == "SSE") &
                            (frame["is_open"].astype(int) == 1)][["cal_date"]])
    dates = sorted(set(pd.concat(frames)["cal_date"].astype(str).str.replace("-", "", regex=False).str[:8]))
    dates = [date for date in dates if "20230101" <= date <= "20261009"]
    recent = dates[-30:]
    tasks = [
        {"phase": "explicit_gaps", "table": "tdx_sector_daily", "status": "blocked_source_qualification", "missing_keys": 51},
        {"phase": "explicit_gaps", "table": "tdx_sector_min", "status": "blocked_source_qualification", "missing_keys": 14},
        {"phase": "explicit_gaps", "table": "ts_daily_basic", "api_name": "daily_basic", "trade_date": "20260928", "status": "candidate_ready"},
        {"phase": "explicit_gaps", "table": "ts_margin_detail", "api_name": "margin_detail", "trade_date": "20261008", "status": "candidate_ready"},
    ]
    for index in range(0, len(recent), 20):
        group = recent[index:index + 20]
        for table, api in (("ts_daily_basic", "daily_basic"), ("ts_margin_detail", "margin_detail")):
            tasks.append({"phase": "recent_30", "batch": index // 20 + 1, "table": table, "api_name": api, "trade_dates": group})
    for year in sorted(set(date[:4] for date in dates)):
        year_dates = [date for date in dates if date.startswith(year)]
        for index in range(0, len(year_dates), 20):
            group = year_dates[index:index + 20]
            for table, api in (("ts_daily_basic", "daily_basic"), ("ts_margin_detail", "margin_detail")):
                tasks.append({"phase": "annual", "year": year, "batch": index // 20 + 1,
                              "table": table, "api_name": api, "trade_dates": group})
    result = {"production_write": False, "global_activation": False, "batch_size": 20,
              "trade_date_count": len(dates), "recent_30": recent, "tasks": tasks}
    output = Path("quant_backtest/data/tushare_staging/history_backfill_plan.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"trade_date_count": len(dates), "task_count": len(tasks), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
