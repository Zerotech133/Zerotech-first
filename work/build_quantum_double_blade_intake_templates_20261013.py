"""生成量子双刀来源补交模板（K-STOCK-214）。

模板只用于收集来源、许可证、本人离线审核和时间切分证据，
不写入训练开关，也不改变现有候选清单的 evaluation_only 状态。
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "work" / "academy-source-evidence-v0.1" / (
    "d-drive-quantum-double-blade-candidate-manifest-v0.1.json"
)
OUT = ROOT / "work" / "academy-source-evidence-v0.1" / "intake"


def load_records() -> list[dict]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    records = data.get("records", [])
    if not records:
        raise ValueError("candidate manifest has no records")
    return records


def base_record(record: dict) -> dict:
    return {
        "sample_id": record["sample_id"],
        "source": record["source"],
        "source_path": record.get("source_path"),
        "sample_sha256": record["sample_sha256"],
        "source_reference": None,
        "source_date": None,
        "source_date_basis": {
            "evidence_type": None,
            "locator": None,
            "exact_quote": None,
            "evidence_sha256": None,
        },
        "license": {
            "license_id": None,
            "license_subject": None,
            "purpose": "strategy_model_evaluation_only",
            "valid_from": None,
            "valid_until": None,
            "commercial_scope": None,
            "license_approved": False,
            "evidence_ref": None,
        },
        "offline_review": {
            "receipt_id": None,
            "reviewer_subject": None,
            "key_fingerprint": None,
            "reviewed_at": None,
            "signature_algorithm": None,
            "signature_value": None,
            "receipt_ref": None,
            "status": "pending",
        },
        "permission_snapshot": {
            "snapshot_id": None,
            "captured_at": None,
            "subject": None,
            "roles": [],
            "scope": None,
            "expires_at": None,
            "snapshot_sha256": None,
            "status": "missing",
        },
        "time_split": {
            "split": None,
            "split_group": None,
            "train_window": {"from": None, "to": None},
            "validation_window": {"from": None, "to": None},
            "test_window": {"from": None, "to": None},
            "no_temporal_overlap_asserted": False,
            "evidence_ref": None,
        },
        "training_eligible": False,
        "strategy_training_eligible": False,
        "production_eligible": False,
        "evaluation_only": True,
    }


def write_json(name: str, payload: dict) -> None:
    (OUT / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    records = [base_record(r) for r in load_records()]
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(
        "source-evidence-intake-template-v0.1.json",
        {
            "schema": "quantum_double_blade_source_evidence_intake/v1",
            "status": "pending_user_evidence",
            "source_root": "D:\\Documents\\xwechat_files\\tianfeng1333_2e0a\\msg\\file",
            "required_fields": [
                "sample_sha256",
                "source_reference",
                "source_date",
                "source_date_basis",
                "license",
                "offline_review",
                "permission_snapshot",
                "time_split",
            ],
            "prohibitions": [
                "不得填写或保存私钥、口令或助记词",
                "不得用文件mtime或文件名日期替代原始发布日期",
                "证据未齐前保持evaluation_only",
            ],
            "records": records,
        },
    )
    write_json(
        "offline-review-receipt-template-v0.1.json",
        {
            "schema": "offline_review_receipt/v1",
            "status": "pending",
            "receipt_rules": [
                "每个样本一条回执，sample_sha256必须与候选清单完全一致",
                "只登记公钥指纹和签名结果，不收集私钥或口令",
                "签名算法、主体和审核范围必须可离线复核",
            ],
            "receipts": [
                {
                    "receipt_id": None,
                    "sample_id": r["sample_id"],
                    "sample_sha256": r["sample_sha256"],
                    "reviewer_subject": None,
                    "review_scope": "source_and_training_use",
                    "reviewed_at": None,
                    "key_fingerprint": None,
                    "signature_algorithm": None,
                    "signature_value": None,
                    "receipt_sha256": None,
                    "status": "pending",
                }
                for r in records
            ],
        },
    )
    write_json(
        "license-registration-template-v0.1.json",
        {
            "schema": "source_license_registration/v1",
            "status": "pending",
            "global_policy": {
                "training_enabled": False,
                "strategy_training_enabled": False,
                "financial_execution_enabled": False,
                "simulated_execution_enabled": False,
                "commercialization_enabled": False,
            },
            "registrations": [
                {
                    "sample_id": r["sample_id"],
                    "sample_sha256": r["sample_sha256"],
                    "license_id": None,
                    "license_subject": None,
                    "purpose": "strategy_model_evaluation_only",
                    "valid_from": None,
                    "valid_until": None,
                    "commercial_scope": None,
                    "evidence_ref": None,
                    "evidence_sha256": None,
                    "license_approved": False,
                }
                for r in records
            ],
        },
    )
    write_json(
        "time-split-template-v0.1.json",
        {
            "schema": "strategy_time_split/v1",
            "status": "pending",
            "allowed_split": ["train", "validation", "test", "evaluation_only"],
            "rules": [
                "split_group用于防止同一来源或同一事件跨集合泄漏",
                "窗口必须使用ISO日期且train早于validation早于test",
                "无法证明时间边界时只能填evaluation_only",
            ],
            "records": [
                {
                    "sample_id": r["sample_id"],
                    "sample_sha256": r["sample_sha256"],
                    "split": None,
                    "split_group": None,
                    "train_window": {"from": None, "to": None},
                    "validation_window": {"from": None, "to": None},
                    "test_window": {"from": None, "to": None},
                    "no_temporal_overlap_asserted": False,
                    "evidence_ref": None,
                }
                for r in records
            ],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
