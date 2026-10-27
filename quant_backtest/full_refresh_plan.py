# -*- coding: utf-8 -*-
"""全量历史更新的交易日分批和 RunStore 计划适配层。"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from warehouse_runs import RunStore


def _date(value: str) -> str:
    text = str(value).replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        raise ValueError("日期必须为 YYYYMMDD")
    datetime.strptime(text, "%Y%m%d")
    return text


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
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
    return [
        days[offset : offset + batch_days] for offset in range(0, len(days), batch_days)
    ]


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
    if (
        start > end
        or not table
        or source not in {"tdx", "tushare"}
        or market not in {"all", "cn", "hk", "us"}
    ):
        raise ValueError("刷新范围或来源不合法")
    days = [
        _date(item)
        for item in (
            trading_days if trading_days is not None else _default_days(start, end)
        )
    ]
    days = sorted({item for item in days if start <= item <= end})
    if not days:
        raise ValueError("交易日范围为空")
    units = []
    for index, window in enumerate(_windows(days, batch_days), start=1):
        scope = {
            "start_date": window[0],
            "end_date": window[-1],
            "trading_days": window,
        }
        units.append(
            {
                "unit_id": f"{table}:{window[0]}-{window[-1]}:{index:04d}",
                "table": table,
                "target": f"{table}/part_{index:04d}.parquet",
                "kind": "window",
                "scope": scope,
                "selection_reason": "user",
            }
        )
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
        "operations": [
            {"table": table, "operation": "paged_incremental", "batch_days": batch_days}
        ],
        "unsupported": [],
        "baseline_digest": baseline_digest,
    }
    plan["plan_digest"] = _digest(plan)
    plan["production_write"] = False
    plan["global_activation"] = False
    return plan


def execute_refresh_batch(
    plan: dict, store_path: str | Path, *, dry_run: bool = True
) -> dict:
    """登记一批刷新任务；dry-run 默认不创建 RunStore 任务或写入仓库。"""
    if (
        not isinstance(plan, dict)
        or not plan.get("units")
        or not plan.get("plan_digest")
    ):
        raise ValueError("刷新计划不完整")
    if dry_run:
        return {
            "dry_run": True,
            "run_id": None,
            "units": len(plan["units"]),
            "production_write": False,
            "global_activation": False,
        }
    run_plan = {
        key: plan[key]
        for key in (
            "version",
            "source",
            "resource",
            "market",
            "date",
            "selected_tables",
            "retry_of",
            "retry_units",
            "registry_digest",
            "targets",
            "units",
            "operations",
            "unsupported",
            "baseline_digest",
            "plan_digest",
        )
    }
    store = RunStore(store_path)
    request_id = "full-refresh:" + plan["plan_digest"]
    run_id = store.create_run(
        run_plan, entry="manual", request_id=request_id, now=time.time()
    )
    return {
        "dry_run": False,
        "run_id": run_id,
        "units": len(plan["units"]),
        "production_write": False,
        "global_activation": False,
    }


_RECENT_KEY_FIELDS = {
    "stock_basic": ("ts_code",),
    "ts_daily_basic": ("ts_code", "trade_date"),
    "ts_margin_detail": ("ts_code", "trade_date"),
    "tdx_sector_daily": ("code", "trade_date"),
    "tdx_sector_min": ("code", "trade_date", "trade_time"),
}


def _recent_key(table: str, row: Mapping[str, Any]) -> tuple[str, ...] | None:
    fields = _RECENT_KEY_FIELDS.get(table)
    if fields is None or not all(field in row for field in fields):
        # 来源可能用 index_code/sector_code 表示板块键；标准化后再检查。
        if table.startswith("tdx_sector") and "code" not in row:
            alias = (
                "index_code"
                if "index_code" in row
                else "sector_code"
                if "sector_code" in row
                else None
            )
            if alias:
                row = dict(row)
                row["code"] = row[alias]
        fields = _RECENT_KEY_FIELDS.get(table)
    if fields is None or not all(field in row for field in fields):
        return None
    return tuple(str(row[field]) for field in fields)


def _recent_status(result: Mapping[str, Any]) -> tuple[str, str]:
    status = str(result.get("status") or "unknown").lower()
    rows = result.get("rows")
    count = result.get("row_count")
    if status in {"qualified", "candidate_ready", "ok", "success"} and count not in (
        0,
        None,
    ):
        if isinstance(rows, list) and rows:
            return "qualified", "source_verified"
        return "blocked", "missing_rows_payload"
    if status in {
        "source_empty",
        "blocked",
        "no_supply",
        "unavailable",
        "source_error",
        "permission_denied",
        "rate_limited",
    }:
        return "blocked", status
    return "rejected", "unknown_probe_status"


def execute_recent_refresh(
    gaps: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    *,
    source_probe: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    trading_days: Iterable[str] | None = None,
    batch_days: int = 30,
    source: str = "tushare",
) -> dict[str, Any]:
    """执行最近窗口的只读来源核验并生成 staging 回执。

    ``source_probe`` 只允许返回实际查询结果；空页、权限、网络和解析错误
    都会进入 blocked/rejected。此函数从不调用 ``commit_table``，也不写入
    生产目录，staging 中的行需要后续受控发布流程再次回读。
    """
    if not 20 <= batch_days <= 30:
        raise ValueError("最近窗口 batch_days 必须在 20 到 30 之间")
    if not callable(source_probe):
        raise TypeError("source_probe 必须可调用")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    staging = target / "staging"
    staging.mkdir(exist_ok=True)
    days = [_date(day) for day in (trading_days or [])]
    windows = _windows(sorted(set(days)), batch_days) if days else []
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    seen_by_table: dict[str, set[tuple[str, ...]]] = {}
    receipts: list[dict[str, Any]] = []
    intents: list[dict[str, Any]] = []
    completes: list[dict[str, Any]] = []
    qualified = blocked = rejected = 0
    duplicate_found = False
    for index, original in enumerate(
        sorted(
            (dict(item) for item in gaps),
            key=lambda item: (
                str(item.get("table", "")),
                str(item.get("trade_date", "")),
                str(item.get("code", "")),
            ),
        ),
        start=1,
    ):
        table = str(original.get("table") or "")
        if not table:
            rejected += 1
            receipts.append(
                {
                    "unit_id": f"recent:{index:04d}",
                    "table": table,
                    "status": "rejected",
                    "reason": "missing_table",
                    "request_scope": original,
                }
            )
            continue
        unit_id = f"recent:{table}:{original.get('trade_date', '')}:{original.get('code', '')}:{index:04d}"
        requested_at = datetime.now().astimezone().isoformat()
        try:
            response = source_probe(dict(original))
            if not isinstance(response, Mapping):
                raise ValueError("探针返回值不是对象")
            status, reason = _recent_status(response)
            response_rows = (
                response.get("rows") if isinstance(response.get("rows"), list) else []
            )
        except Exception as exc:  # 探针异常必须可恢复地落在回执中
            status, reason, response, response_rows = (
                "rejected",
                "probe_error",
                {"error": f"{type(exc).__name__}: {exc}"},
                [],
            )
        row_count = (
            int(response.get("row_count", len(response_rows)) or 0)
            if isinstance(response, Mapping)
            else 0
        )
        receipt = {
            "unit_id": unit_id,
            "table": table,
            "source": source,
            "request_scope": {
                key: original.get(key)
                for key in ("code", "trade_date", "trade_time")
                if key in original
            },
            "requested_at": requested_at,
            "received_at": datetime.now().astimezone().isoformat(),
            "status": status,
            "source_status": str(response.get("status", "probe_error"))
            if isinstance(response, Mapping)
            else "probe_error",
            "result_rows": row_count,
            "reason": reason,
            "source_reason": response.get("reason")
            if isinstance(response, Mapping)
            else None,
            "evidence": response.get("evidence")
            if isinstance(response, Mapping)
            else None,
        }
        if status == "qualified":
            table_rows = rows_by_table.setdefault(table, [])
            seen = seen_by_table.setdefault(table, set())
            for row in response_rows:
                if not isinstance(row, Mapping):
                    status, reason = "rejected", "row_not_object"
                    break
                fact_key = _recent_key(table, row)
                if fact_key is None:
                    status, reason = "rejected", "primary_key_unresolved"
                    break
                if fact_key in seen:
                    status, reason = "rejected", "duplicate_key"
                    duplicate_found = True
                    break
                seen.add(fact_key)
                table_rows.append(dict(row))
            if status == "qualified":
                qualified += 1
            elif status == "blocked":
                blocked += 1
            else:
                rejected += 1
        elif status == "blocked":
            blocked += 1
        else:
            rejected += 1
        receipt["status"], receipt["reason"] = status, reason
        receipts.append(receipt)
        intent = {
            "unit_id": unit_id,
            "table": table,
            "status": "staging_only" if status == "qualified" else "blocked",
            "production_write": False,
        }
        complete = {
            "unit_id": unit_id,
            "table": table,
            "status": "not_published" if status == "qualified" else "blocked",
            "production_write": False,
        }
        intents.append(intent)
        completes.append(complete)

    for table, table_rows in rows_by_table.items():
        (staging / f"{table}.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in table_rows
            ),
            encoding="utf-8",
        )
    for name, payload in (
        ("source_receipt.jsonl", receipts),
        ("commit_intent.jsonl", intents),
        ("commit_complete.jsonl", completes),
    ):
        (target / name).write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in payload
            ),
            encoding="utf-8",
        )
    overall = "blocked" if blocked or rejected or duplicate_found else "staged"
    verification = {
        "status": overall,
        "reason": "duplicate_key"
        if duplicate_found
        else "source_qualification_failed"
        if blocked
        else "probe_rejected"
        if rejected
        else None,
        "qualified": qualified,
        "blocked": blocked,
        "rejected": rejected,
        "staging_rows": {table: len(rows) for table, rows in rows_by_table.items()},
        "windows": [len(window) for window in windows],
        "production_write": False,
        "global_activation": False,
        "publication": "not_started",
    }
    (target / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return verification


# 兼容计划文档中的动词命名，保持两个入口都指向同一只读实现。
run_recent_refresh = execute_recent_refresh
