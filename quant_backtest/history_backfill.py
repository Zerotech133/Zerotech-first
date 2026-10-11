# -*- coding: utf-8 -*-
"""历史数据只读回补器。

该模块只把 TuShare 返回写入 ``data/tushare_staging/history``，不会调用
生产仓同步器，也不会修改 ``data/tushare``。每个交易日独立保存 parquet
和 receipt，重复执行时优先复用完整回执。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Iterable, Mapping, Sequence

import pandas as pd
import requests

API_URL = "https://api.tushare.pro"
HERE = Path(__file__).resolve().parent
DEFAULT_STAGE = HERE / "data" / "tushare_staging" / "history"


def _date(value: object) -> str:
    value = str(value).replace("-", "")[:8]
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"日期格式错误: {value!r}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _token() -> str:
    value = os.environ.get("TUSHARE_TOKEN", "").strip()
    if value:
        return value
    env_file = HERE / ".env.local"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("XT_DATA_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise RuntimeError("缺少 TUSHARE_TOKEN 或 quant_backtest/.env.local XT_DATA_KEY")


def validate_frame(frame: pd.DataFrame, primary_key: Sequence[str], expected_date: str) -> dict[str, object]:
    expected_date = _date(expected_date)
    missing = [key for key in primary_key if key not in frame.columns]
    if missing:
        raise ValueError(f"缺少主键列: {missing}")
    if frame[list(primary_key)].isna().any().any():
        raise ValueError("主键存在空值")
    duplicates = int(frame.duplicated(list(primary_key), keep=False).sum())
    if duplicates:
        raise ValueError(f"主键重复: {duplicates}")
    if "trade_date" not in frame.columns:
        raise ValueError("缺少 trade_date")
    dates = frame["trade_date"].dropna().map(_date).unique().tolist()
    if dates and dates != [expected_date]:
        raise ValueError(f"日期越界: expected={expected_date}, actual={sorted(dates)}")
    return {
        "row_count": int(len(frame)),
        "unique_keys": int(len(frame.drop_duplicates(list(primary_key)))),
        "duplicate_keys": 0,
        "date_values": sorted(dates),
        "null_primary_key": {key: int(frame[key].isna().sum()) for key in primary_key},
        "columns": [str(column) for column in frame.columns],
    }


def _post(api_name: str, params: Mapping[str, object], timeout: int = 60, retries: int = 3) -> tuple[dict, bytes]:
    payload = {"api_name": api_name, "token": _token(), "params": dict(params), "fields": ""}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.post(API_URL, json=payload, timeout=timeout)
            raw = response.content
            response.raise_for_status()
            return response.json(), raw
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"TuShare 请求失败: {type(last).__name__}") from last


def _receipt(table: str, api_name: str, params: Mapping[str, object], frame: pd.DataFrame,
             body_sha256: str, source_code: int, source_message: str,
             primary_key: Sequence[str]) -> dict[str, object]:
    expected = str(params.get("trade_date", ""))
    result = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "table": table, "api_name": api_name, "request_params": dict(params),
        "source_code": source_code, "source_message": source_message,
        "body_sha256": body_sha256, "production_write": False,
    }
    if source_code != 0:
        result.update({"status": "source_error", "complete": False, "row_count": 0})
        return result
    if frame.empty:
        result.update({"status": "source_empty", "complete": False, "row_count": 0,
                       "unique_keys": 0, "duplicate_keys": 0})
        return result
    try:
        result.update(validate_frame(frame, primary_key, expected))
    except ValueError as exc:
        result.update({"status": "invalid_source", "complete": False, "error": str(exc),
                       "row_count": int(len(frame))})
        return result
    result.update({"status": "candidate_ready", "complete": True})
    return result


def pull_one(table: str, api_name: str, trade_date: str, stage_dir: str | Path = DEFAULT_STAGE,
             primary_key: Sequence[str] = ("ts_code", "trade_date")) -> dict[str, object]:
    trade_date = _date(trade_date)
    stage_dir = Path(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    parquet = stage_dir / f"{table}_{trade_date}.parquet"
    receipt_path = stage_dir / f"{table}_{trade_date}.receipt.json"
    if parquet.exists() and receipt_path.exists():
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        if saved.get("complete") and saved.get("staged_sha256"):
            saved["reused"] = True
            return saved

    payload, raw = _post(api_name, {"trade_date": trade_date})
    data = payload.get("data") or {}
    frame = pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])
    receipt = _receipt(table, api_name, {"trade_date": trade_date}, frame,
                       _sha256_bytes(raw), int(payload.get("code", -1)),
                       str(payload.get("msg", "")), primary_key)
    if receipt["status"] == "candidate_ready":
        temporary = parquet.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, parquet)
        receipt["staged_path"] = str(parquet.resolve())
        receipt["staged_sha256"] = _sha256_bytes(parquet.read_bytes())
    receipt["receipt_path"] = str(receipt_path.resolve())
    temporary_receipt = receipt_path.with_suffix(".json.tmp")
    temporary_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_receipt, receipt_path)
    return receipt


def run_batch(table: str, api_name: str, dates: Iterable[str], stage_dir: str | Path = DEFAULT_STAGE) -> dict[str, object]:
    receipts = [pull_one(table, api_name, day, stage_dir) for day in dates]
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "table": table, "api_name": api_name, "production_write": False,
        "entry_count": len(receipts),
        "complete_count": sum(bool(item.get("complete")) for item in receipts),
        "source_empty_count": sum(item.get("status") == "source_empty" for item in receipts),
        "source_error_count": sum(item.get("status") == "source_error" for item in receipts),
        "entries": receipts,
    }
    path = Path(stage_dir) / f"{table}_run_manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="TuShare 历史数据隔离 staging 回补")
    parser.add_argument("--table", choices=["ts_daily_basic", "ts_margin_detail"], required=True)
    parser.add_argument("--api-name", choices=["daily_basic", "margin_detail"], required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE)
    args = parser.parse_args()
    print(json.dumps(run_batch(args.table, args.api_name, args.dates, args.stage_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
