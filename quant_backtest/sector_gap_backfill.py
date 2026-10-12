# -*- coding: utf-8 -*-
"""TDX 板块日线缺口的只读探针与 staging 适配层。"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd


REQUIRED_COLUMNS = {"code", "trade_date", "open", "high", "low", "close", "vol", "amount"}


def _payload_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _request_parameters(key: Mapping[str, Any]) -> dict[str, Any]:
    code, trade_date = str(key.get("code", "")), str(key.get("trade_date", ""))
    return {"code": code, "start_date": trade_date, "end_date": trade_date, "limit": 2, "offset": 0}


def _valid_date(value: Any) -> bool:
    text = str(value)
    try:
        return len(text) == 8 and text.isdigit() and datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d") == text
    except ValueError:
        return False


def _result(status: str, key: Mapping[str, Any], *, reason: str | None = None,
            response: Any = None, row: Mapping[str, Any] | None = None,
            error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table": "tdx_sector_daily",
        "key": {"code": str(key.get("code", "")), "trade_date": str(key.get("trade_date", ""))},
        "status": status,
        "production_write": False,
        "request_parameters": _request_parameters(key),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if response is not None:
        result["raw_response"] = response
        result["source_response_json_sha256"] = _payload_sha(response)
    if reason:
        result["reason"] = reason
    if error:
        result["error"] = error
    if row is not None:
        result["row"] = dict(row)
        result["row_count"] = 1
    else:
        result["row_count"] = 0
    return result


def validate_sector_daily_response(key: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """校验远端单键响应；返回 candidate_ready/source_empty/invalid_source。"""
    if not isinstance(response, Mapping):
        return _result("invalid_source", key, reason="response_not_object", response=response)
    rows = response.get("rows")
    columns = response.get("columns")
    total = response.get("total")
    if not isinstance(rows, list) or not isinstance(columns, list) or len(columns) != len(set(columns)):
        return _result("invalid_source", key, reason="response_contract_invalid", response=response)
    if not isinstance(total, int) or isinstance(total, bool) or total != len(rows):
        return _result("invalid_source", key, reason="total_mismatch", response=response)
    if total == 0:
        return _result("source_empty", key, reason="source_empty", response=response)
    if total != 1:
        return _result("invalid_source", key, reason="expected_exactly_one_row", response=response)
    if not REQUIRED_COLUMNS.issubset(set(columns)) or len(rows[0]) != len(columns):
        return _result("invalid_source", key, reason="required_columns_missing", response=response)
    row = dict(zip(columns, rows[0]))
    expected = (str(key.get("code", "")), str(key.get("trade_date", "")))
    if (str(row.get("code")), str(row.get("trade_date"))) != expected:
        return _result("invalid_source", key, reason="key_mismatch", response=response, row=row)
    if not _valid_date(row.get("trade_date")):
        return _result("invalid_source", key, reason="invalid_trade_date", response=response, row=row)
    for field in REQUIRED_COLUMNS:
        if row.get(field) is None or (isinstance(row.get(field), float) and not math.isfinite(row[field])):
            return _result("invalid_source", key, reason=f"null_or_nonfinite_{field}", response=response, row=row)
    return _result("candidate_ready", key, response=response, row=row)


def probe_sector_daily(key: Mapping[str, Any], fetch: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """执行一个板块日线只读探针；默认复用 tdx_sync 的 GET，不触发同步写入。"""
    if fetch is None:
        import tdx_sync
        response = tdx_sync._get("/api/data/table/tdx_sector_daily", _request_parameters(key))
    else:
        response = fetch(key)
    return validate_sector_daily_response(key, response)


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    return [dict(item) for item in value]


def stage_sector_daily(keys: Sequence[Mapping[str, Any]], stage_root: str | Path,
                       probe: Callable[[Mapping[str, Any]], Mapping[str, Any]] = probe_sector_daily) -> dict[str, Any]:
    """逐键探针并写入隔离 staging；不写 ``data/tdx`` 生产目录。"""
    root = Path(stage_root)
    raw_dir = root / "raw"
    data_dir = root / "tdx_sector_daily"
    raw_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    identities = [(str(item.get("code", "")), str(item.get("trade_date", ""))) for item in keys]
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []
    receipt_rows: list[dict[str, Any]] = []
    counts = {"added": 0, "blocked": 0, "rejected": 0, "source_empty": 0}
    for key in sorted(keys, key=lambda item: (str(item.get("trade_date", "")), str(item.get("code", "")))):
        identity = (str(key.get("code", "")), str(key.get("trade_date", "")))
        if identity in seen:
            result = _result("invalid_source", key, reason="duplicate_requested_key")
        else:
            seen.add(identity)
            try:
                result = probe(key)
                if isinstance(result, Mapping) and "rows" in result and "columns" in result:
                    result = validate_sector_daily_response(key, result)
                if not isinstance(result, Mapping) or "status" not in result:
                    result = _result("invalid_source", key, reason="probe_result_invalid")
            except Exception as exc:
                result = _result("invalid_source", key, reason="probe_error", error=f"{type(exc).__name__}: {exc}")
        result = dict(result)
        raw = result.get("raw_response")
        if raw is not None:
            raw_path = raw_dir / f"{identity[0]}_{identity[1]}.json"
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            result["raw_path"] = str(raw_path)
        status = str(result.get("status"))
        if status == "candidate_ready" and result.get("row"):
            row = dict(result["row"])
            row.update({
                "actual_received_at": result.get("captured_at_utc"),
                "source_provider": "tdx-trading-table",
                "source_endpoint": "/api/data/table/tdx_sector_daily",
                "source_request_parameters": json.dumps(result.get("request_parameters", {}), sort_keys=True),
                "source_response_json_sha256": result.get("source_response_json_sha256"),
            })
            candidates.append(row)
            counts["added"] += 1
        elif status == "source_empty":
            counts["source_empty"] += 1
            counts["blocked"] += 1
        elif status == "invalid_source" and result.get("reason") in {"total_mismatch", "expected_exactly_one_row"}:
            # 远端忽略单键过滤时返回整日/整表结果；无法把行安全归因到请求键，
            # 这是来源能力阻断而非数据本身损坏。
            counts["blocked"] += 1
            status = "blocked"
        else:
            counts["rejected"] += 1
        receipt_rows.append({
            "code": identity[0], "trade_date": identity[1], "status": status,
            "reason": result.get("reason"), "source_response_json_sha256": result.get("source_response_json_sha256"),
        })

    if candidates:
        pd.DataFrame(candidates).sort_values(["trade_date", "code"]).to_parquet(data_dir / "part_0000.parquet", index=False)
    receipt = {
        "table": "tdx_sector_daily", "production_write": False, "global_activation": False,
        "counts": counts, "rows": receipt_rows, "candidate_rows": candidates,
    }
    (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"table": "tdx_sector_daily", "production_write": False, "global_activation": False,
            "counts": counts, "receipt_path": str(root / "receipt.json"), "rows": receipt_rows}


def reconcile_sector_definition(definition_rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
                               daily_rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
                               validity_rows: Iterable[Mapping[str, Any]] | pd.DataFrame) -> dict[str, Any]:
    """按板块有效期核对历史日线，避免用当前定义覆盖历史。"""
    definitions = _records(definition_rows)
    daily = _records(daily_rows)
    validity = _records(validity_rows)
    codes = {str(row.get("code")) for row in definitions if row.get("code") is not None}
    windows: dict[str, list[tuple[str | None, str | None]]] = {code: [] for code in codes}
    for row in validity:
        code = str(row.get("code"))
        if code not in codes:
            codes.add(code)
            windows[code] = []
        start = row.get("valid_from") or row.get("start_date") or row.get("effective_date")
        end = row.get("valid_to") or row.get("end_date")
        windows[code].append((str(start)[:8] if start else None, str(end)[:8] if end else None))

    def active(code: str, date: str) -> bool:
        if not windows.get(code):
            return code in codes
        return any((not start or date >= start) and (not end or date <= end) for start, end in windows[code])

    by_date: dict[str, set[str]] = {}
    for row in daily:
        code, date = str(row.get("code")), str(row.get("trade_date"))
        by_date.setdefault(date, set()).add(code)
    missing: list[tuple[str, str]] = []
    extra: list[tuple[str, str]] = []
    for date, actual in sorted(by_date.items()):
        expected = {code for code in codes if active(code, date)}
        missing.extend((code, date) for code in sorted(expected - actual))
        extra.extend((code, date) for code in sorted(actual - expected))
    return {"checked_dates": sorted(by_date), "missing": sorted(missing), "extra": sorted(extra),
            "missing_count": len(missing), "extra_count": len(extra), "production_write": False}

