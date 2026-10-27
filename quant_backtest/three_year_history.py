# -*- coding: utf-8 -*-
"""三年历史数据的计划、审计和隔离 staging 执行器。

该模块只依赖本地交易日历和 ``history_backfill`` 的 TuShare 请求函数，所有
产物写入指定 staging 目录；任何入口都不会触碰生产仓。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

import history_backfill as hb


@dataclass(frozen=True)
class TableSpec:
    name: str
    api_name: str
    primary_key: tuple[str, ...] = ("ts_code", "trade_date")


def _date(value: object) -> str:
    text = str(value).replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"日期格式错误: {value!r}")
    return text


def load_trade_dates(calendar_root: Path, start: str, end: str) -> list[str]:
    """读取 SSE 开市日，并验证日期集合没有重复。"""
    start, end = _date(start), _date(end)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    frames = []
    seen_snapshots: set[str] = set()
    for path in sorted(Path(calendar_root).glob("*.parquet")):
        snapshot_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if snapshot_hash in seen_snapshots:
            continue
        seen_snapshots.add(snapshot_hash)
        frame = pd.read_parquet(path)
        required = {"exchange", "cal_date", "is_open"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"交易日历缺少列: {missing}: {path}")
        frames.append(frame[list(required)])
    if not frames:
        raise ValueError(f"交易日历为空: {calendar_root}")
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[
        (frame["exchange"].astype(str) == "SSE") & (frame["is_open"].astype(int) == 1)
    ]
    dates = [_date(value) for value in frame["cal_date"].tolist()]
    counts = Counter(dates)
    duplicates = sorted(date for date, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"重复交易日: {duplicates[:5]}")
    return sorted(date for date in dates if start <= date <= end)


def build_batches(
    dates: Sequence[str], batch_size: int = 40
) -> list[dict[str, object]]:
    """按固定大小切分交易日；最后一批允许小于 20 个日期。"""
    if not 20 <= int(batch_size) <= 50:
        raise ValueError("batch_size 必须在 20 到 50 之间")
    normalized = [_date(value) for value in dates]
    if normalized != sorted(set(normalized)):
        raise ValueError("交易日必须按升序且不重复")
    return [
        {
            "batch_id": f"batch_{index:03d}",
            "trade_dates": normalized[offset : offset + batch_size],
        }
        for index, offset in enumerate(range(0, len(normalized), batch_size), 1)
    ]


def _load_receipt(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _exists(value: object, fallback: Path) -> bool:
    path = Path(str(value)) if value else fallback
    return path.exists() and path.is_file()


def audit_batches(
    stage_root: Path, batches: Sequence[dict], tables: Sequence[str]
) -> dict[str, object]:
    missing: list[str] = []
    complete = 0
    total = 0
    for batch in batches:
        batch_id = str(batch["batch_id"])
        for table in tables:
            for date in batch["trade_dates"]:
                total += 1
                base = Path(stage_root) / batch_id
                receipt_path = base / f"{table}_{date}.receipt.json"
                receipt = _load_receipt(receipt_path)
                raw_fallback = base / f"{table}_{date}.json"
                parquet_fallback = base / f"{table}_{date}.parquet"
                valid = bool(
                    receipt
                    and receipt.get("complete") is True
                    and receipt.get("status") == "candidate_ready"
                )
                valid = valid and _exists(
                    receipt.get("raw_path") if receipt else None, raw_fallback
                )
                valid = valid and _exists(
                    receipt.get("staged_path") if receipt else None, parquet_fallback
                )
                key = f"{batch_id}:{table}:{date}"
                if valid:
                    complete += 1
                else:
                    missing.append(key)
    return {
        "stage_root": str(Path(stage_root).resolve()),
        "total_count": total,
        "complete_count": complete,
        "missing": missing,
        "production_write": False,
        "global_activation": False,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def pull_one_with_raw(
    table: str,
    api_name: str,
    trade_date: str,
    stage_dir: str | Path,
    primary_key: Sequence[str],
) -> dict[str, object]:
    """请求一个交易日并同时保存原始响应、Parquet 和回执。"""
    trade_date = _date(trade_date)
    stage_dir = Path(stage_dir)
    raw_path = stage_dir / f"{table}_{trade_date}.json"
    receipt_path = stage_dir / f"{table}_{trade_date}.receipt.json"
    parquet_path = stage_dir / f"{table}_{trade_date}.parquet"
    saved = _load_receipt(receipt_path)
    if (
        saved
        and saved.get("complete") is True
        and _exists(saved.get("raw_path"), raw_path)
        and _exists(saved.get("staged_path"), parquet_path)
    ):
        saved["reused"] = True
        return saved

    payload, raw = hb._post(api_name, {"trade_date": trade_date})
    _atomic_write(raw_path, raw)
    data = payload.get("data") or {}
    frame = pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])
    receipt = hb._receipt(
        table,
        api_name,
        {"trade_date": trade_date},
        frame,
        hashlib.sha256(raw).hexdigest(),
        int(payload.get("code", -1)),
        str(payload.get("msg", "")),
        primary_key,
    )
    receipt.update(
        {
            "raw_path": str(raw_path.resolve()),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "receipt_path": str(receipt_path.resolve()),
        }
    )
    if receipt.get("status") == "candidate_ready":
        temporary = parquet_path.with_name(parquet_path.name + ".tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, parquet_path)
        receipt.update(
            {
                "staged_path": str(parquet_path.resolve()),
                "staged_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
            }
        )
    else:
        receipt["staged_path"] = ""
    _atomic_write(
        receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    )
    return receipt


def _reuse_or_pull(
    spec: TableSpec, date: str, batch_dir: Path
) -> tuple[dict[str, object], bool]:
    receipt_path = batch_dir / f"{spec.name}_{date}.receipt.json"
    receipt = _load_receipt(receipt_path)
    raw_path = batch_dir / f"{spec.name}_{date}.json"
    parquet_path = batch_dir / f"{spec.name}_{date}.parquet"
    if (
        receipt
        and receipt.get("complete") is True
        and receipt.get("status") == "candidate_ready"
        and _exists(receipt.get("raw_path"), raw_path)
        and _exists(receipt.get("staged_path"), parquet_path)
    ):
        receipt["reused"] = True
        return receipt, True
    result = pull_one_with_raw(
        spec.name, spec.api_name, date, batch_dir, spec.primary_key
    )
    if not receipt_path.exists():
        _atomic_write(
            receipt_path,
            json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return result, bool(result.get("reused"))


def run_three_year(
    table_specs: Sequence[TableSpec],
    dates: Iterable[str],
    stage_root: str | Path,
    batch_size: int = 40,
) -> dict[str, object]:
    dates = [_date(value) for value in dates]
    batches = build_batches(dates, batch_size)
    stage_root = Path(stage_root)
    entries: list[dict[str, object]] = []
    reused_count = 0
    for batch in batches:
        batch_dir = stage_root / str(batch["batch_id"])
        batch_entries = []
        for spec in table_specs:
            for date in batch["trade_dates"]:
                receipt, reused = _reuse_or_pull(spec, date, batch_dir)
                reused_count += int(reused)
                batch_entries.append(receipt)
        manifest = {
            "batch_id": batch["batch_id"],
            "table_count": len(table_specs),
            "date_count": len(batch["trade_dates"]),
            "entries": batch_entries,
            "production_write": False,
        }
        _atomic_write(
            batch_dir / "run_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        entries.extend(batch_entries)
    result = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage_root": str(stage_root.resolve()),
        "batch_count": len(batches),
        "entry_count": len(entries),
        "complete_count": sum(bool(item.get("complete")) for item in entries),
        "reused_count": reused_count,
        "entries": entries,
        "production_write": False,
        "global_activation": False,
    }
    _atomic_write(
        stage_root / "run_manifest.json",
        json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return result
