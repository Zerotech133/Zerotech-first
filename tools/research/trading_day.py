# -*- coding: utf-8 -*-
"""交易日判断（SSE trade_cal）。

用于成本告警等只在交易日运行的监控任务：
  python tools/research/trading_day.py check            # 今天
  python tools/research/trading_day.py check --date 2026-11-05 --json

退出码：0=交易日（开市）；1=非交易日；2=日期缺失/数据异常。
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

REPO = Path(r"C:\项目\老田的AI助手")
DEFAULT_DIR = REPO / "quant_backtest" / "data" / "tushare" / "trade_cal"
EXCHANGE = "SSE"


def _normalize_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"日期格式应为 YYYYMMDD 或 YYYY-MM-DD：{value!r}")
    return text


def load_trade_cal(data_dir: str | Path | None = None, exchange: str = EXCHANGE) -> pd.DataFrame:
    """读取并去重交易日历；返回列 exchange/cal_date/is_open/pretrade_date。"""
    root = Path(data_dir) if data_dir else DEFAULT_DIR
    files = sorted(glob.glob(str(root / "**" / "*.parquet"), recursive=True))
    if not files:
        raise FileNotFoundError(f"交易日历目录无 parquet：{root}")
    frames = []
    keep = ["exchange", "cal_date", "is_open", "pretrade_date"]
    for path in files:
        frame = pd.read_parquet(path)
        cols = {"exchange", "cal_date", "is_open"}
        if not cols.issubset(frame.columns):
            continue
        frames.append(frame[[c for c in keep if c in frame.columns]])
    if not frames:
        raise ValueError(f"交易日历无有效表：{root}")
    df = pd.concat(frames, ignore_index=True)
    df["exchange"] = df["exchange"].astype(str)
    df["cal_date"] = df["cal_date"].astype(str).str.zfill(8)
    df = df[df["exchange"] == exchange].drop_duplicates("cal_date", keep="last")
    return df.sort_values("cal_date").reset_index(drop=True)


def status_for(day: str | date | datetime, data_dir: str | Path | None = None,
               exchange: str = EXCHANGE) -> dict:
    cal_date = _normalize_date(day)
    df = load_trade_cal(data_dir=data_dir, exchange=exchange)
    row = df[df["cal_date"] == cal_date]
    if row.empty:
        raise ValueError(f"交易日历无 {cal_date}（{exchange}）")
    is_open = int(row.iloc[0]["is_open"]) == 1
    return {
        "exchange": exchange,
        "cal_date": cal_date,
        "is_trading_day": is_open,
        "is_open": 1 if is_open else 0,
        "source": str(Path(data_dir) if data_dir else DEFAULT_DIR),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SSE 交易日判断")
    parser.add_argument("cmd", nargs="?", default="check", choices=("check",))
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="YYYY-MM-DD，默认今天")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = status_for(args.date, data_dir=args.data_dir)
    except Exception as exc:  # noqa: BLE001 - CLI 需要稳定退出码
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}")
        return 2
    if args.json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    else:
        state = "OPEN" if result["is_trading_day"] else "CLOSED"
        print(f"{state} {result['cal_date']} exchange={result['exchange']}")
    return 0 if result["is_trading_day"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
