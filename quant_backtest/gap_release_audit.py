# -*- coding: utf-8 -*-
"""缺口 staging 回执和发布门槛审计。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def _receipt_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = payload.get("keys")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def audit_gap_release(stage_root: str | Path, baseline: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(stage_root)
    files = sorted({path for path in ([root / "receipt.json"] if (root / "receipt.json").exists() else []) + list(root.rglob("receipt.json"))})
    reasons: list[str] = []
    if not files:
        reasons.append("missing_receipt")
    receipt_payloads: list[Mapping[str, Any]] = []
    row_count = 0
    pending_count = 0
    blocked_count = 0
    rejected_count = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            reasons.append(f"invalid_receipt:{path.name}")
            continue
        receipt_payloads.append(payload)
        if payload.get("production_write") is not False or payload.get("global_activation") is not False:
            reasons.append(f"guard_violation:{path.name}")
        rows = _receipt_rows(payload)
        row_count += len(rows)
        for row in rows:
            status = str(row.get("status", ""))
            if status == "pending":
                pending_count += 1
            elif status in {"blocked", "source_empty", "source_error"}:
                blocked_count += 1
            elif status in {"rejected", "invalid_source"}:
                rejected_count += 1
    expected_total = int((baseline.get("counts") or {}).get("total", 0))
    if expected_total and row_count < expected_total:
        reasons.append("uncovered_keys")
    if pending_count:
        reasons.append("pending_keys")
    if rejected_count:
        reasons.append("rejected_keys")
    # blocked 键是可审计但不可发布的状态；在当前阶段必须阻断发布。
    if blocked_count:
        reasons.append("blocked_keys")
    publishable = not reasons and bool(files) and bool(baseline.get("production_write") is False)
    return {"stage_root": str(root), "receipt_files": [str(path) for path in files], "receipt_count": len(files),
            "expected_total": expected_total, "receipt_rows": row_count, "pending_count": pending_count,
            "blocked_count": blocked_count, "rejected_count": rejected_count, "blocked_reasons": sorted(set(reasons)),
            "publishable": publishable, "production_write": False, "global_activation": False}


def assert_publish_gates(audit: Mapping[str, Any]) -> None:
    if not audit.get("publishable"):
        reasons = ", ".join(str(item) for item in audit.get("blocked_reasons", [])) or "unknown"
        raise RuntimeError(f"发布门未通过: {reasons}")

