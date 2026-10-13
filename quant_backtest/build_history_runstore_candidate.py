# -*- coding: utf-8 -*-
"""为历史回补 staging 生成 registry 审计和 RunStore 候选运行。

本入口只登记来源回执和候选计划，不登记 commit_intent，不触碰生产仓。
"""
from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
STAGE = HERE / "data" / "tushare_staging"
REGISTRY = STAGE / "tushare_qualification_candidate.json"
AUDIT = STAGE / "runstore_candidate_audit.json"
PLAN_OUT = STAGE / "runstore_candidate_plan.json"
RUNS = STAGE / "warehouse_runs.sqlite3"

sys.path.insert(0, str(HERE))
from warehouse_runs import RunStore  # noqa: E402
from warehouse_update import metadata_digest, plan_digest  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(value: str | None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _registry_audit(registry: dict, registry_path: Path = REGISTRY) -> dict:
    rows = {row.get("asset_id"): row for row in registry.get("source_registry", {}).get("physical_tables", [])}
    checks = {}
    for table in ("ts_daily_basic", "ts_margin_detail"):
        row = rows.get(table) or {}
        evidence = row.get("qualification_evidence") or {}
        checks[table] = {
            "provider": row.get("provider"),
            "channel": row.get("channel"),
            "qualification_status": row.get("qualification_status"),
            "production_write": bool(registry.get("production_write")),
            "global_activation": bool(registry.get("global_activation")),
            "required_pending_fields": row.get("pending_fields", []),
            "evidence_receipts": len(evidence.get("explicit_receipts", [])),
            "reconciliation": bool(evidence.get("reconciliation")),
        }
        checks[table]["gate_pass"] = bool(
            row.get("provider") == "tushare.pro"
            and row.get("channel") == "tushare"
            and row.get("qualification_status") == "candidate_ready"
            and registry.get("production_write") is False
            and registry.get("global_activation") is False
            and evidence.get("explicit_receipts")
            and evidence.get("reconciliation")
        )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": _sha(registry_path),
        "global_activation": registry.get("global_activation"),
        "production_write": registry.get("production_write"),
        "checks": checks,
        "all_candidate_gates_pass": all(item["gate_pass"] for item in checks.values()),
        "production_publish_allowed": False,
        "reason": "仍缺 RunStore 运行完成、资源锁释放、发布锁、发布后指纹和回读对账",
    }


def _complete_receipts(stage_root: Path = STAGE) -> list[tuple[str, str, Path, dict]]:
    entries = []
    for table, api in (("ts_daily_basic", "daily_basic"), ("ts_margin_detail", "margin_detail")):
        for receipt_path in sorted(stage_root.glob(f"annual_*/*{table}_*.receipt.json")):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("complete") is not True or receipt.get("status") != "candidate_ready":
                continue
            date = str(receipt.get("request_params", {}).get("trade_date", ""))
            if len(date) != 8:
                continue
            entries.append((table, api, receipt_path, receipt))
    return entries


def build(*, registry_path: Path = REGISTRY, stage_root: Path = STAGE) -> dict:
    """生成候选运行；registry 可来自主工作区，产物只写入 staging_root。"""
    registry_path = Path(registry_path).resolve()
    stage_root = Path(stage_root).resolve()
    audit_path = stage_root / "runstore_candidate_audit.json"
    plan_path = stage_root / "runstore_candidate_plan.json"
    runs_path = stage_root / "warehouse_runs.sqlite3"
    result_path = stage_root / "runstore_candidate_result.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    audit = _registry_audit(registry, registry_path)
    stage_root.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    entries = _complete_receipts(stage_root)
    units = []
    for table, _api, receipt_path, receipt in entries:
        date = str(receipt["request_params"]["trade_date"])
        units.append({
            "unit_id": f"{table}:{date}",
            "table": table,
            "target": str(receipt_path.with_name(receipt_path.name.replace(".receipt.json", ".parquet")).resolve()),
            "kind": "date",
            "scope": {"trade_date": date},
            "selection_reason": "dependency",
        })
    units.sort(key=lambda item: item["unit_id"])
    registry_digest = metadata_digest(registry)
    plan = {
        "version": 1,
        "source": "tushare",
        "resource": str(stage_root),
        "market": "cn",
        "date": "2026-10-11",
        "selected_tables": ["ts_daily_basic", "ts_margin_detail"],
        "retry_of": None,
        "retry_units": [],
        "registry_digest": registry_digest,
        "targets": [item["target"] for item in units],
        "units": units,
        "operations": [
            {"table": "ts_daily_basic", "api_name": "daily_basic", "operation": "date_append_staging"},
            {"table": "ts_margin_detail", "api_name": "margin_detail", "operation": "date_append_staging"},
        ],
        "unsupported": [
            {"table": "tdx_sector_daily", "missing_keys": 51, "status": "blocked_source_qualification"},
            {"table": "tdx_sector_min", "missing_keys": 14, "status": "blocked_source_qualification"},
        ],
        "baseline_digest": _sha(stage_root / "reconciliation" / "report.json"),
    }
    plan["plan_digest"] = plan_digest(plan)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    now = datetime.now(timezone.utc).timestamp()
    store = RunStore(runs_path)
    request_id = f"K-STOCK-116-history-candidate-{plan['plan_digest'][:16]}"
    run_id = store.create_run(plan, entry="manual", request_id=request_id, now=now)
    for table, api, receipt_path, receipt in entries:
        date = str(receipt["request_params"]["trade_date"])
        unit_id = f"{table}:{date}"
        captured = _iso(receipt.get("captured_at_utc"))
        store.event(run_id, "source_receipt", {
            "receipt_id": f"staging:{table}:{date}:{receipt.get('body_sha256', '')[:16]}",
            "run_id": run_id,
            "unit_id": unit_id,
            "table": table,
            "provider": "tushare.pro",
            "channel": "tushare",
            "source_id": "https://api.tushare.pro",
            "api_name": api,
            "request_scope": {"trade_date": date},
            "requested_at": captured,
            "received_at": captured,
            "source_time": {"value": captured, "timezone": "UTC", "kind": "response_time", "evidence": str(receipt_path.resolve())},
            "result_rows": int(receipt.get("row_count", 0)),
            "result_kind": "authorized_data",
            "error_kind": None,
        }, now=now)
    result = {
        "run_id": run_id,
        "plan_digest": plan["plan_digest"],
        "unit_count": len(units),
        "registry_audit": str(audit_path),
        "plan_path": str(plan_path),
        "runstore_path": str(runs_path),
        "production_write": False,
        "global_activation": False,
        "publication_lock": "not_started",
        "post_publish_fingerprint": "not_started",
        "readback_reconciliation": "not_started",
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成历史回补 staging 候选运行")
    parser.add_argument("--registry", type=Path, default=REGISTRY,
                        help="只读来源 registry 路径，默认使用当前工作树候选 registry")
    parser.add_argument("--stage-root", type=Path, default=STAGE,
                        help="候选产物和 staging 回执目录，默认当前工作树 staging")
    args = parser.parse_args()
    print(json.dumps(build(registry_path=args.registry, stage_root=args.stage_root), ensure_ascii=False))
