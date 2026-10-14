import hashlib
import json

from build_history_runstore_candidate import _registry_audit, build


def _candidate_registry():
    rows = []
    for table in ("ts_daily_basic", "ts_margin_detail"):
        rows.append({
            "asset_id": table,
            "provider": "tushare.pro",
            "channel": "tushare",
            "qualification_status": "candidate_ready",
            "pending_fields": ["runstore_run_id"],
            "qualification_evidence": {
                "explicit_receipts": [f"{table}.receipt.json"],
                "reconciliation": "reconciliation/report.json",
            },
        })
    return {
        "global_activation": False,
        "production_write": False,
        "source_registry": {"physical_tables": rows},
    }


def test_build_reads_external_registry_and_keeps_it_unchanged(tmp_path):
    registry_path = tmp_path / "main" / "tushare_qualification_candidate.json"
    registry_path.parent.mkdir()
    registry_path.write_text(json.dumps(_candidate_registry()), encoding="utf-8")
    before = registry_path.read_bytes()
    stage_root = tmp_path / "staging"
    (stage_root / "reconciliation").mkdir(parents=True)
    (stage_root / "reconciliation" / "report.json").write_text("{}", encoding="utf-8")

    result = build(registry_path=registry_path, stage_root=stage_root)

    assert registry_path.read_bytes() == before
    audit = json.loads((stage_root / "runstore_candidate_audit.json").read_text(encoding="utf-8"))
    assert audit["registry_sha256"] == hashlib.sha256(before).hexdigest()
    assert audit["all_candidate_gates_pass"] is True
    assert result["production_write"] is False
    assert result["global_activation"] is False
    assert result["publication_lock"] == "not_started"
    assert result["readback_reconciliation"] == "not_started"


def test_build_reads_external_receipts_without_writing_source_stage(tmp_path):
    registry_path = tmp_path / "main" / "tushare_qualification_candidate.json"
    registry_path.parent.mkdir()
    registry_path.write_text(json.dumps(_candidate_registry()), encoding="utf-8")
    source_root = tmp_path / "source-staging"
    batch = source_root / "annual_2026_batch_001"
    batch.mkdir(parents=True)
    (source_root / "reconciliation").mkdir()
    (source_root / "reconciliation" / "report.json").write_text("{}", encoding="utf-8")
    receipt = {
        "complete": True,
        "status": "candidate_ready",
        "request_params": {"trade_date": "20261009"},
        "body_sha256": "a" * 64,
        "row_count": 1,
        "captured_at_utc": "2026-10-09T08:00:00+00:00",
    }
    for table in ("ts_daily_basic", "ts_margin_detail"):
        (batch / f"{table}_20261009.receipt.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
    before = sorted(p.relative_to(source_root).as_posix() for p in source_root.rglob("*"))
    output_root = tmp_path / "candidate-output"

    result = build(registry_path=registry_path, stage_root=output_root, receipt_root=source_root)

    after = sorted(p.relative_to(source_root).as_posix() for p in source_root.rglob("*"))
    assert before == after
    assert result["unit_count"] == 2
    plan = json.loads((output_root / "runstore_candidate_plan.json").read_text(encoding="utf-8"))
    assert plan["resource"] == str(source_root.resolve())
    assert {u["unit_id"] for u in plan["units"]} == {
        "ts_daily_basic:20261009", "ts_margin_detail:20261009"
    }


def test_registry_audit_reason_matches_candidate_gate_state(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_candidate_registry()), encoding="utf-8")

    audit = _registry_audit(_candidate_registry(), registry_path)

    assert audit["all_candidate_gates_pass"] is True
    assert "候选来源门禁已通过" in audit["reason"]
    assert "未允许生产发布" in audit["reason"]
    assert "runstore_run_id" in audit["reason"]
