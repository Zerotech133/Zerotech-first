# -*- coding: utf-8 -*-
"""缺口快照与来源资格基线。

本模块只读取完整性报告并生成隔离清单，不访问远端，也不写入生产数据目录。
所有空响应、来源错误和探针异常都保留为明确的 blocked/rejected 状态，不能被
转换成成功或补零数据。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


_METRIC_NAMES = {
    "tdx_sector_daily_daily_coverage": ("tdx_sector_daily", "tdx", "code"),
    "sector_minute_daily_coverage": ("tdx_sector_minute", "tdx", "code"),
    "ts_margin_detail_daily_coverage": ("ts_margin_detail", "tushare", "ts_code"),
}


def _metrics(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """兼容直接 scan 报告和 all_sources 汇总报告。"""
    candidate: Any = report.get("metrics")
    if not isinstance(candidate, list):
        integrity = report.get("integrity")
        candidate = integrity.get("metrics") if isinstance(integrity, Mapping) else None
    if not isinstance(candidate, list):
        raise ValueError("完整性报告缺少 metrics 列表")
    return [item for item in candidate if isinstance(item, Mapping) and item.get("name")]


def _metric_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["name"]): item for item in _metrics(report)}


def _dates(details: Mapping[str, Any]):
    for value in sorted(details):
        if value == "_summary":
            continue
        if len(str(value)) == 8 and str(value).isdigit():
            item = details[value]
            if isinstance(item, Mapping):
                yield str(value), item


def _append_coverage(keys: list[dict[str, Any]], metric: Mapping[str, Any], table: str,
                     source: str, code_field: str) -> None:
    details = metric.get("details")
    if not isinstance(details, Mapping):
        return
    for trade_date, day in _dates(details):
        missing = day.get("missing", [])
        if not isinstance(missing, Sequence) or isinstance(missing, (str, bytes)):
            continue
        for code in missing:
            if code is None or str(code) == "_summary":
                continue
            keys.append({
                "table": table,
                "source": source,
                "market": "A",
                "code": str(code),
                "trade_date": trade_date,
                "status": "pending",
                "reason": "source_empty_pending" if day.get("source_state") == "source_empty_pending" else "coverage_gap",
            })


def _append_latest_stock(keys: list[dict[str, Any]], metric: Mapping[str, Any]) -> None:
    details = metric.get("details")
    if not isinstance(details, Mapping):
        return
    trade_date = str(details.get("date", ""))
    missing = details.get("missing", [])
    if not trade_date or not isinstance(missing, Sequence) or isinstance(missing, (str, bytes)):
        return
    for code in missing:
        if code is None:
            continue
        keys.append({
            "table": "listed_stock",
            "source": "tushare",
            "market": "A",
            "code": str(code),
            "trade_date": trade_date,
            "status": "pending",
            "reason": "coverage_gap",
        })


def _sort_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(field, "")) for field in ("table", "trade_date", "code", "source"))


def build_gap_snapshot(integrity_report: Mapping[str, Any]) -> dict[str, Any]:
    """从健康扫描报告抽取明确缺口，返回稳定排序的隔离快照。"""
    metrics = _metric_map(integrity_report)
    keys: list[dict[str, Any]] = []
    for metric_name, (table, source, code_field) in _METRIC_NAMES.items():
        metric = metrics.get(metric_name)
        # 日线 coverage 在旧版扫描中只有 missing_count，具体代码由定义对账指标提供。
        if metric_name == "tdx_sector_daily_daily_coverage":
            has_codes = any(
                isinstance(day, Mapping) and isinstance(day.get("missing"), Sequence)
                and not isinstance(day.get("missing"), (str, bytes))
                for date, day in _dates(metric.get("details", {}))
            ) if metric else False
            if not has_codes:
                metric = metrics.get("sector_definition_key_reconciliation")
        if metric:
            _append_coverage(keys, metric, table, source, code_field)
    latest = metrics.get("listed_stock_latest_coverage")
    if latest:
        _append_latest_stock(keys, latest)

    keys.sort(key=_sort_key)
    # 同一份报告若重复列出键，保留首次记录并使数量可复现。
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in keys:
        identity = (row["table"], row["trade_date"], row["code"])
        if identity not in seen:
            seen.add(identity)
            unique.append(row)

    counts = {
        "tdx_sector_daily": sum(row["table"] == "tdx_sector_daily" for row in unique),
        "tdx_sector_minute": sum(row["table"] == "tdx_sector_minute" for row in unique),
        "ts_margin_detail": sum(row["table"] == "ts_margin_detail" for row in unique),
        "listed_stock": sum(row["table"] == "listed_stock" for row in unique),
    }
    counts["total"] = sum(counts.values())
    return {
        "generated_at": integrity_report.get("generated_at"),
        "expected_latest": integrity_report.get("expected_latest")
        or (integrity_report.get("integrity", {}) or {}).get("expected_latest"),
        "production_write": False,
        "global_activation": False,
        "counts": counts,
        "keys": unique,
    }


def write_key_manifest(snapshot: Mapping[str, Any], path: str | Path) -> None:
    """写入 JSON 清单；传入原始报告时自动先生成快照。"""
    if "keys" not in snapshot or "counts" not in snapshot:
        snapshot = build_gap_snapshot(snapshot)
    if snapshot.get("production_write") is not False:
        raise ValueError("缺口清单必须保持 production_write=false")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def qualify_source(table: str, keys: Sequence[Mapping[str, Any]],
                   probe: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> dict[str, Any]:
    """逐键执行只读探针并分类资格，绝不把空响应视为有效数据。"""
    qualified: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for original in sorted(keys, key=_sort_key):
        row = dict(original)
        row["table"] = table
        try:
            result = probe(row)
            if not isinstance(result, Mapping):
                raise ValueError("探针返回值不是对象")
            status = str(result.get("status", "")).lower()
            row["source_status"] = status or "unknown"
            if status in {"source_empty", "blocked", "source_error", "no_supply", "unavailable"}:
                row["status"] = "blocked"
                row["reason"] = status
                blocked.append(row)
            elif status in {"candidate_ready", "qualified", "ok", "success"} and result.get("row_count", 1) != 0:
                row["status"] = "qualified"
                row["reason"] = "source_verified"
                qualified.append(row)
            elif status == "invalid_source":
                row["status"] = "rejected"
                row["reason"] = "invalid_source"
                rejected.append(row)
            else:
                row["status"] = "rejected"
                row["reason"] = "unknown_probe_status"
                rejected.append(row)
        except Exception as exc:  # 探针异常也必须留在回执中
            row["status"] = "rejected"
            row["reason"] = "probe_error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            rejected.append(row)

    return {
        "table": table,
        "production_write": False,
        "global_activation": False,
        "counts": {"qualified": len(qualified), "blocked": len(blocked), "rejected": len(rejected)},
        "keys": qualified + blocked + rejected,
    }

