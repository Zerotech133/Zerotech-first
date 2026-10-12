# -*- coding: utf-8 -*-
"""全量历史更新的交易日分批和 RunStore 计划适配层。"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from warehouse_runs import RunStore


def _date(value: str) -> str:
    text = str(value).replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        raise ValueError("日期必须为 YYYYMMDD")
    datetime.strptime(text, "%Y%m%d")
    return text


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _default_days(start: str, end: str) -> list[str]:
    begin = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:8]}")
    finish = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:8]}")
    days = []
    cursor = begin
    while cursor <= finish:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return days


def _windows(days: list[str], batch_days: int) -> list[list[str]]:
    if not 20 <= batch_days <= 50:
        raise ValueError("batch_days 必须在 20 到 50 之间")
    return [days[offset:offset + batch_days] for offset in range(0, len(days), batch_days)]


def build_full_refresh_plan(
    table: str,
    start_date: str,
    end_date: str,
    batch_days: int = 30,
    *,
    trading_days: Iterable[str] | None = None,
    source: str = "tushare",
    market: str = "cn",
    resource: str | None = None,
    registry_digest: str = "registry-unverified",
    baseline_digest: str = "baseline-unverified",
) -> dict:
    """生成稳定、可恢复的窗口计划；默认只生成计划，不触发请求或写入。"""
    start, end = _date(start_date), _date(end_date)
    if start > end or not table or source not in {"tdx", "tushare"} or market not in {"all", "cn", "hk", "us"}:
        raise ValueError("刷新范围或来源不合法")
    days = [_date(item) for item in (trading_days if trading_days is not None else _default_days(start, end))]
    days = sorted({item for item in days if start <= item <= end})
    if not days:
        raise ValueError("交易日范围为空")
    units = []
    for index, window in enumerate(_windows(days, batch_days), start=1):
        scope = {"start_date": window[0], "end_date": window[-1], "trading_days": window}
        units.append({
            "unit_id": f"{table}:{window[0]}-{window[-1]}:{index:04d}",
            "table": table,
            "target": f"{table}/part_{index:04d}.parquet",
            "kind": "window",
            "scope": scope,
            "selection_reason": "user",
        })
    plan = {
        "version": 1,
        "source": source,
        "resource": resource or f"{source}:warehouse",
        "market": market,
        "date": end,
        "selected_tables": [table],
        "retry_of": None,
        "retry_units": [],
        "registry_digest": registry_digest,
        "targets": [unit["target"] for unit in units],
        "units": units,
        "operations": [{"table": table, "operation": "paged_incremental", "batch_days": batch_days}],
        "unsupported": [],
        "baseline_digest": baseline_digest,
    }
    plan["plan_digest"] = _digest(plan)
    plan["production_write"] = False
    plan["global_activation"] = False
    return plan


def execute_refresh_batch(plan: dict, store_path: str | Path, *, dry_run: bool = True) -> dict:
    """登记一批刷新任务；dry-run 默认不创建 RunStore 任务或写入仓库。"""
    if not isinstance(plan, dict) or not plan.get("units") or not plan.get("plan_digest"):
        raise ValueError("刷新计划不完整")
    if dry_run:
        return {
            "dry_run": True,
            "run_id": None,
            "units": len(plan["units"]),
            "production_write": False,
            "global_activation": False,
        }
    run_plan = {key: plan[key] for key in (
        "version", "source", "resource", "market", "date", "selected_tables", "retry_of",
        "retry_units", "registry_digest", "targets", "units", "operations", "unsupported",
        "baseline_digest", "plan_digest")}
    store = RunStore(store_path)
    request_id = "full-refresh:" + plan["plan_digest"]
    run_id = store.create_run(run_plan, entry="manual", request_id=request_id, now=time.time())
    return {
        "dry_run": False,
        "run_id": run_id,
        "units": len(plan["units"]),
        "production_write": False,
        "global_activation": False,
    }
