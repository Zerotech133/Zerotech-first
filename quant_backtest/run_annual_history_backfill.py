# -*- coding: utf-8 -*-
"""执行年度历史回补的一批交易日；已有 receipt 会自动复用。"""
from pathlib import Path
import json
import pandas as pd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history_backfill import run_batch


def main() -> None:
    calendar_root = Path(r"E:\老田的AI助手\quant_backtest\data\tushare\trade_cal")
    frames = []
    for path in sorted(calendar_root.glob("*.parquet")):
        frame = pd.read_parquet(path, columns=["exchange", "cal_date", "is_open"])
        frames.append(frame[(frame["exchange"].astype(str) == "SSE") &
                            (frame["is_open"].astype(int) == 1)][["cal_date"]])
    dates = sorted(set(pd.concat(frames)["cal_date"].astype(str).str.replace("-", "", regex=False).str[:8]))
    dates = [date for date in dates if date.startswith("2023")][:20]
    stage = Path("quant_backtest/data/tushare_staging/annual_2023_batch_001")
    result = {"dates": dates, "production_write": False, "tables": {}}
    for table, api in (("ts_daily_basic", "daily_basic"), ("ts_margin_detail", "margin_detail")):
        result["tables"][table] = run_batch(table, api, dates, stage)
    Path("quant_backtest/data/tushare_staging/annual_2023_batch_001_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dates": dates, "tables": {
        table: {key: value[key] for key in ("entry_count", "complete_count", "source_empty_count", "source_error_count")}
        for table, value in result["tables"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
