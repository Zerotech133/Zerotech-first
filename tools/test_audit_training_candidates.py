import hashlib
import json
from pathlib import Path

from audit_training_candidates import audit_manifest


def _record(root: Path, name: str, *, split="train", group="g1"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("candidate content\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "sample_id": f"sample-{name}",
        "source": name,
        "source_path": name,
        "sample_sha256": digest,
        "license_id": "lic-001",
        "license_subject": "老田",
        "purpose": "isolated_training",
        "valid_from": "2026-01-01",
        "valid_until": "2027-01-01",
        "commercial_scope": "internal_evaluation",
        "source_reference": f"local://{name}",
        "split": split,
        "split_group": group,
        "offline_review_receipt": {
            "receipt_id": "receipt-001",
            "subject": "老田",
            "signed_at": "2026-10-10T10:00:00+08:00",
            "signature": "offline-signature",
            "public_key_fingerprint": "a" * 64,
        },
        "license_approved": True,
        "permission_snapshot": {
            "snapshot_id": "perm-001",
            "subject": "老田",
            "valid_until": "2027-01-01",
            "sha256": "b" * 64,
        },
    }


def _manifest(tmp_path, records, *, training_enabled=False):
    root = tmp_path / "sources"
    root.mkdir(exist_ok=True)
    for item in records:
        _record(root, item["source_path"])
    return {
        "schema_version": "training-eligible-manifest-v0.1",
        "source_root": str(root),
        "policy": {"training_enabled": training_enabled, "as_of": "2026-10-12"},
        "records": records,
    }


def test_complete_record_is_content_eligible_but_global_training_off(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    result = audit_manifest(_manifest(tmp_path, [record]))

    assert result["counts"] == {
        "total": 1,
        "content_eligible": 1,
        "training_eligible": 0,
        "strategy_training_eligible": 0,
        "evaluation_only": 1,
    }
    assert result["records"][0]["training_eligible"] is False
    assert result["records"][0]["strategy_training_eligible"] is False
    assert result["strategy_training_eligible"] is False
    assert "training_disabled" in result["records"][0]["reasons"]


def test_complete_record_can_enter_strategy_training_without_financial_training(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record], training_enabled=False)
    manifest["policy"]["strategy_training_enabled"] = True

    result = audit_manifest(manifest)

    assert result["policy"]["strategy_training_enabled"] is True
    assert result["strategy_training_eligible"] is True
    assert result["counts"]["strategy_training_eligible"] == 1
    assert result["records"][0]["strategy_training_eligible"] is True
    assert result["records"][0]["evaluation_only"] is False
    assert result["records"][0]["training_eligible"] is False
    assert result["policy"]["financial_execution_enabled"] is False


def test_strategy_training_is_blocked_when_simulated_execution_is_enabled(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record], training_enabled=False)
    manifest["policy"].update({
        "strategy_training_enabled": True,
        "simulated_execution_enabled": True,
    })

    result = audit_manifest(manifest)

    assert result["strategy_training_eligible"] is False
    assert result["counts"]["strategy_training_eligible"] == 0
    assert result["policy"]["simulated_execution_enabled"] is True
    assert "strategy_training_requires_simulated_execution_off" in result["records"][0]["reasons"]


def test_strategy_training_is_blocked_when_traditional_training_is_enabled(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record], training_enabled=True)
    manifest["policy"]["strategy_training_enabled"] = True

    result = audit_manifest(manifest)

    assert result["strategy_training_eligible"] is False
    assert result["counts"]["strategy_training_eligible"] == 0
    assert "strategy_training_requires_training_off" in result["records"][0]["reasons"]


def test_missing_license_and_split_stays_evaluation_only(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    for field in ("license_id", "license_approved", "split", "split_group"):
        record.pop(field, None)

    result = audit_manifest(_manifest(tmp_path, [record]))
    reasons = set(result["records"][0]["reasons"])

    assert result["counts"]["content_eligible"] == 0
    assert result["counts"]["evaluation_only"] == 1
    assert {"missing:license_id", "missing:license_approved",
            "missing:split", "missing:split_group"} <= reasons


def test_source_hash_drift_blocks_candidate(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record])
    (tmp_path / "sources" / "a.md").write_text("drift\n", encoding="utf-8")

    result = audit_manifest(manifest)

    assert result["counts"]["content_eligible"] == 0
    assert "sample_hash_mismatch" in result["records"][0]["reasons"]


def test_split_group_cross_split_is_blocked(tmp_path):
    first = _record(tmp_path / "sources", "a.md", split="train", group="same")
    second = _record(tmp_path / "sources", "b.md", split="test", group="same")
    result = audit_manifest(_manifest(tmp_path, [first, second]))

    assert result["counts"]["content_eligible"] == 0
    assert all("split_group_cross_split" in item["reasons"]
               for item in result["records"])


def test_audit_result_is_json_serializable(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    result = audit_manifest(_manifest(tmp_path, [record]))
    json.dumps(result, ensure_ascii=False)


def test_entries_alias_is_accepted_for_rebuilt_candidate_manifest(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record])
    manifest.pop("records")
    manifest["entries"] = [record]

    result = audit_manifest(manifest)

    assert result["counts"]["total"] == 1
    assert result["records"][0]["sample_id"] == record["sample_id"]


def test_source_name_is_used_when_source_path_is_omitted(tmp_path):
    record = _record(tmp_path / "sources", "a.md")
    manifest = _manifest(tmp_path, [record])
    manifest["records"][0].pop("source_path")

    result = audit_manifest(manifest)

    assert result["counts"]["content_eligible"] == 1
    assert "source_missing" not in result["records"][0]["reasons"]
