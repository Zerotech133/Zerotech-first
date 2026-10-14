# -*- coding: utf-8 -*-
"""融资融券缺口资格分类与日期对账（只读）。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    if isinstance(value, Mapping):
        return [dict(value)]
    return [dict(row) for row in (value or [])]


def _day(value: Any) -> str:
    return str(value or "").replace("-", "")[:8]


def _find(rows: Any, code: str, date: str | None = None) -> list[dict[str, Any]]:
    result = []
    for row in _records(rows):
        row_code = str(row.get("ts_code", row.get("code", "")))
        row_date = _day(row.get("trade_date", row.get("date", "")))
        if row_code == code and (date is None or row_date == date):
            result.append(row)
    return result


def classify_margin_gap(key: Mapping[str, Any], stock_basic: Any, suspend_d: Any, delist_evidence: Any) -> str:
    code, date = str(key.get("ts_code", key.get("code", ""))), _day(key.get("trade_date"))
    basic = _find(stock_basic, code)
    if not basic:
        return "unknown_security"
    row = basic[-1]
    if _day(row.get("list_date")) and _day(row.get("list_date")) > date:
        return "prelisting"
    delisted = _find(delist_evidence, code)
    if isinstance(delist_evidence, Mapping) and code in delist_evidence:
        delisted = _records(delist_evidence[code])
    if any(not _day(item.get("effective_date", item.get("trade_date"))) or _day(item.get("effective_date", item.get("trade_date"))) <= date for item in delisted):
        return "delisted"
    if _find(suspend_d, code, date):
        return "suspended"
    flag = next((row.get(name) for name in ("is_margin", "margin_eligible", "is_margin_eligible", "margin_flag") if name in row), None)
    if flag is not None and str(flag).strip().lower() in {"0", "false", "n", "no"}:
        return "non_margin"
    return "eligible_missing"


def _receipt_rows(receipt: Any) -> list[dict[str, Any]]:
    if isinstance(receipt, Mapping):
        rows = receipt.get("rows", receipt.get("data", receipt.get("keys", [])))
        return [dict(row) for row in rows] if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else []
    return _records(receipt)


def reconcile_margin_dates(expected_dates: Sequence[str], receipts: Any, eligibility: Any) -> dict[str, Any]:
    """按日期和资格集合重算融资覆盖，保留来源阻断信息。"""
    if isinstance(receipts, Mapping):
        receipt_map = {str(k): v for k, v in receipts.items()}
    else:
        receipt_map = {}
        for item in receipts or []:
            if isinstance(item, Mapping):
                receipt_map[_day(item.get("trade_date", item.get("date")))] = item
    missing: list[dict[str, str]] = []
    blocked_dates: list[str] = []
    source_empty_dates: list[str] = []
    observed_by_date: dict[str, set[str]] = {}
    for date in sorted(_day(value) for value in expected_dates):
        expected = eligibility.get(date, set()) if isinstance(eligibility, Mapping) else eligibility
        expected_codes = {str(code) for code in (expected or [])}
        receipt = receipt_map.get(date, {})
        status = str(receipt.get("status", "")) if isinstance(receipt, Mapping) else ""
        if status in {"blocked", "source_error", "unavailable"}:
            blocked_dates.append(date)
        if status == "source_empty":
            source_empty_dates.append(date)
        observed = {str(row.get("ts_code", row.get("code", ""))) for row in _receipt_rows(receipt)}
        observed.discard("")
        observed_by_date[date] = observed
        missing.extend({"trade_date": date, "ts_code": code} for code in sorted(expected_codes - observed))
    return {"expected_dates": sorted(_day(value) for value in expected_dates), "observed_by_date": {k: sorted(v) for k, v in observed_by_date.items()},
            "missing": missing, "missing_count": len(missing), "blocked_dates": blocked_dates,
            "source_empty_dates": source_empty_dates, "production_write": False}


def reconcile_latest_stock_coverage(trade_date: str, daily: Any, stock_basic: Any, suspend_d: Any) -> dict[str, Any]:
    date = _day(trade_date)
    active: set[str] = set()
    for row in _records(stock_basic):
        code = str(row.get("ts_code", row.get("code", "")))
        if not code or (_day(row.get("list_date")) and _day(row.get("list_date")) > date):
            continue
        if str(row.get("list_status", "L")).upper() not in {"L", "LISTED", ""}:
            continue
        delist = _day(row.get("delist_date"))
        if delist and delist <= date:
            continue
        active.add(code)
    observed = {str(row.get("ts_code", row.get("code", ""))) for row in _records(daily)}
    observed.discard("")
    suspended = sorted(code for code in active if _find(suspend_d, code, date))
    missing = sorted(active - observed - set(suspended))
    return {"date": date, "listed": len(active), "observed": len(active & observed),
            "eligible_suspended": suspended, "missing_expected": missing,
            "missing_expected_count": len(missing), "production_write": False}

