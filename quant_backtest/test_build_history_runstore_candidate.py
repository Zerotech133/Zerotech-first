import hashlib
import json

from build_history_runstore_candidate import build


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
