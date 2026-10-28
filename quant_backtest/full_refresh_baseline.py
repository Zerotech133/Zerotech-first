# -*- coding: utf-8 -*-
"""全量刷新前的只读基线和真实键查询。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


TARGET_TABLES = (
    "stock_basic",
    "ts_daily",
    "ts_daily_basic",
    "ts_margin_detail",
    "tdx_sector_daily",
    "tdx_sector_min",
    "income",
    "top10_holders",
    "top10_floatholders",
    "news",
)


def _manifest(root: Path) -> dict[str, Any]:
    path = root / "_manifest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _table_dir(
    data_root: Path, table: str
) -> tuple[Path | None, str | None, dict[str, Any]]:
    for source in ("tdx", "tushare"):
        root = data_root / source
        candidate = root / table
        if candidate.is_dir():
            return candidate, source, _manifest(root).get(table) or {}
    return None, None, {}


def _normalise_date(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace("-", "", regex=False).str.slice(0, 8)


def _key_columns(frame: pd.DataFrame) -> list[str]:
    for candidate in (
        ["ts_code", "trade_date"],
        ["ts_code", "trade_time"],
        ["ts_code", "ann_date", "end_date", "report_type", "comp_type", "end_type"],
        ["ts_code", "ann_date", "end_date", "holder_name", "holder_type"],
        ["ts_code", "ann_date", "end_date", "holder_name"],
        ["ts_code", "end_date"],
        ["ts_code", "ann_date"],
        ["ts_code"],
        ["code", "trade_date", "trade_time"],
        ["code", "trade_date"],
        ["code", "trade_time"],
        ["code"],
        ["datetime", "title"],
        ["index_code", "trade_date"],
        ["sector_code", "trade_date"],
    ):
        if set(candidate).issubset(frame.columns):
            return candidate
    return []


def _date_column(frame: pd.DataFrame) -> str | None:
    for name in (
        "trade_date",
        "trade_time",
        "ann_date",
        "end_date",
        "pub_date",
        "date",
    ):
        if name in frame.columns:
            return name
    return None


def _scan_table(path: Path, manifest: dict[str, Any], source: str) -> dict[str, Any]:
    files = sorted(path.rglob("*.parquet"))
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for file in files:
        try:
            frames.append(pd.read_parquet(file))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{file.name}:{type(exc).__name__}")
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    keys = _key_columns(frame)
    date_col = _date_column(frame)
    actual_latest = None
    actual_min = None
    if date_col and not frame.empty:
        dates = _normalise_date(frame[date_col]).dropna()
        if not dates.empty:
            actual_min, actual_latest = str(dates.min()), str(dates.max())
    duplicate_keys = (
        int(frame.duplicated(keys).sum()) if keys and not frame.empty else 0
    )
    unique_keys = (
        int(frame[keys].drop_duplicates().shape[0]) if keys and not frame.empty else 0
    )
    actual_rows = int(len(frame))
    reasons = []
    if not files:
        reasons.append("no_local_file")
    if errors:
        reasons.append("read_error")
    if not keys:
        reasons.append("primary_key_unresolved")
    if duplicate_keys:
        reasons.append("duplicate_key")
    qualification = (
        "qualified"
        if actual_rows and keys and not reasons
        else "candidate_ready"
        if actual_rows and keys
        else "blocked"
    )
    if duplicate_keys or errors or not files:
        qualification = "blocked"
    return {
        "table": path.name,
        "source": source,
        "files": len(files),
        "actual_rows": actual_rows,
        "manifest_rows": manifest.get("rows"),
        "manifest_done": bool(manifest.get("done")),
        "columns": list(frame.columns),
        "primary_key": keys,
        "date_column": date_col,
        "actual_min": actual_min,
        "actual_latest": actual_latest,
        "unique_keys": unique_keys,
        "duplicate_keys": duplicate_keys,
        "query_checks": {
            "count": actual_rows,
            "unique_keys": unique_keys,
            "min_date": actual_min,
            "max_date": actual_latest,
        },
        "missing_source_evidence": reasons,
        "qualification": qualification,
        "read_errors": errors,
    }


def build_full_refresh_baseline(
    data_root: str | Path, *, tables: tuple[str, ...] = TARGET_TABLES
) -> dict[str, Any]:
    """扫描关键仓表并执行真实行数、日期和唯一键对账；全程只读。"""
    root = Path(data_root).resolve()
    result: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "data_root": str(root),
        "production_write": False,
        "global_activation": False,
        "tables": {},
    }
    for table in tables:
        path, source, manifest = _table_dir(root, table)
        if path is None:
            result["tables"][table] = {
                "table": table,
                "source": None,
                "files": 0,
                "actual_rows": 0,
                "manifest_rows": None,
                "manifest_done": False,
                "columns": [],
                "primary_key": [],
                "date_column": None,
                "actual_min": None,
                "actual_latest": None,
                "unique_keys": 0,
                "duplicate_keys": 0,
                "query_checks": {
                    "count": 0,
                    "unique_keys": 0,
                    "min_date": None,
                    "max_date": None,
                },
                "missing_source_evidence": ["no_local_file"],
                "qualification": "blocked",
                "read_errors": [],
            }
        else:
            result["tables"][table] = _scan_table(path, manifest, source or "unknown")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root", default=str(Path(__file__).resolve().parent / "data")
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    payload = build_full_refresh_baseline(args.data_root)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")
