# -*- coding: utf-8 -*-
"""生成并执行三年 TuShare 历史数据的隔离 staging 批次。"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from three_year_history import (  # noqa: E402
    TableSpec,
    audit_batches,
    build_batches,
    load_trade_dates,
    run_three_year,
)

DEFAULT_CALENDAR = HERE / "data" / "tushare" / "trade_cal"
DEFAULT_STAGE = HERE / "data" / "tushare_staging" / "history_3y"
DEFAULT_WORK = HERE.parent / "work"
TABLES = {
    "ts_daily": TableSpec("ts_daily", "daily"),
    "ts_daily_basic": TableSpec("ts_daily_basic", "daily_basic"),
    "ts_margin_detail": TableSpec("ts_margin_detail", "margin_detail"),
    "ts_moneyflow": TableSpec("ts_moneyflow", "moneyflow"),
    "ts_index_daily": TableSpec("ts_index_daily", "index_daily"),
    "ts_adj_factor": TableSpec("ts_adj_factor", "adj_factor"),
}


def make_plan(
    calendar_root: str | Path,
    start: str,
    end: str,
    batch_size: int,
    table_names: list[str],
) -> dict[str, object]:
    dates = load_trade_dates(Path(calendar_root), start, end)
    batches = build_batches(dates, batch_size)
    unknown = sorted(set(table_names) - set(TABLES))
    if unknown:
        raise ValueError(f"不支持的表: {unknown}")
    return {
        "created_at_utc": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "start": start,
        "end": end,
        "trade_date_count": len(dates),
        "batch_size": batch_size,
        "batch_count": len(batches),
        "tables": table_names,
        "batches": batches,
        "production_write": False,
        "global_activation": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TuShare 三年历史数据隔离 staging 下载"
    )
    parser.add_argument("--calendar-root", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--stage-root", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--start", default="20230101")
    parser.add_argument("--end", default=None)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument(
        "--table", dest="tables", action="append", choices=sorted(TABLES), default=None
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # 先读完整日历，默认结束日取昨天以前的最后一个开市日，避免把今天或未来
    # 的计划日期当成已完成历史；需要特殊边界时由调用方显式传入 --end。
    all_dates = load_trade_dates(args.calendar_root, args.start, "29991231")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    completed_dates = [value for value in all_dates if value <= yesterday]
    if not completed_dates:
        raise ValueError("交易日历中没有昨天以前的可用交易日")
    end = args.end or completed_dates[-1]
    table_names = args.tables or [
        "ts_daily",
        "ts_daily_basic",
        "ts_margin_detail",
        "ts_moneyflow",
        "ts_index_daily",
        "ts_adj_factor",
    ]
    plan = make_plan(args.calendar_root, args.start, end, args.batch_size, table_names)
    args.stage_root.mkdir(parents=True, exist_ok=True)
    plan_path = args.stage_root / "plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    DEFAULT_WORK.mkdir(parents=True, exist_ok=True)
    (DEFAULT_WORK / "three_year_history_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audit = audit_batches(args.stage_root, plan["batches"], table_names)
    audit_path = DEFAULT_WORK / "three_year_history_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "plan_path": str(plan_path.resolve()),
                    "audit_path": str(audit_path.resolve()),
                    "missing_count": len(audit["missing"]),
                    **{
                        key: plan[key]
                        for key in (
                            "trade_date_count",
                            "batch_count",
                            "tables",
                            "production_write",
                            "global_activation",
                        )
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0

    specs = [TABLES[name] for name in table_names]
    result = run_three_year(
        specs,
        [date for batch in plan["batches"] for date in batch["trade_dates"]],
        args.stage_root,
        args.batch_size,
    )
    result["plan_path"] = str(plan_path.resolve())
    audit = audit_batches(args.stage_root, plan["batches"], table_names)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result_path = DEFAULT_WORK / "three_year_history_run.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "mode": "staging",
                "result_path": str(result_path.resolve()),
                "audit_path": str(audit_path.resolve()),
                "batch_count": result["batch_count"],
                "entry_count": result["entry_count"],
                "complete_count": result["complete_count"],
                "reused_count": result["reused_count"],
                "missing_count": len(audit["missing"]),
                "production_write": False,
                "global_activation": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
