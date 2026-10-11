import hashlib
import json
from pathlib import Path

import pytest

from rebuild_academy_source_snapshot import build_snapshot, source_id_for


def _record(root: Path, relative_path: str, content: str) -> dict:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    raw = path.read_bytes()
    return {
        "path": relative_path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _inputs(tmp_path: Path, *, with_evidence: bool = True):
    root = tmp_path / "e"
    relative = "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md"
    record = _record(root, relative, "# 文档日期：2026-10-11\n内容")
    index = tmp_path / "index.jsonl"
    index.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    payload = {
        "schema": "academy_source_evidence_ledger/v1",
        "entries": [
            {
                "relative_path": relative,
                "source_date": "2026-10-11",
                "observed_at": "2026-10-11T15:00:00+08:00",
                "evidence_ref": "local://docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md#header",
            }
        ]
        if with_evidence
        else [],
    }
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return root, index, evidence


def test_source_id_is_stable_from_relative_path():
    assert source_id_for("docs/audit/example.md") == source_id_for("docs/audit/example.md")
    assert source_id_for("docs/audit/example.md").startswith("E-")


def test_snapshot_includes_evidence_metadata_and_remains_candidate_only(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert result["sources"][0]["source_id"] == source_id_for(
        "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md"
    )
    assert result["sources"][0]["source_date"] == "2026-10-11"
    assert result["sources"][0]["evidence_ref"].startswith("local://")
    assert result["snapshot"]["training_eligible"] is False
    assert result["manifest"]["production_eligible"] is False


def test_missing_evidence_blocks_snapshot(tmp_path):
    root, index, evidence = _inputs(tmp_path, with_evidence=False)
    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert "source_evidence_missing" in result["blockers"]
    assert result["snapshot"]["training_eligible"] is False


def test_hash_drift_blocks_snapshot(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    (root / "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md").write_text(
        "漂移", encoding="utf-8"
    )

    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert "hash_drift" in result["blockers"]
    assert result["sources"] == []


def test_hash_drift_reports_the_affected_path(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    relative = "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md"
    (root / relative).write_text("漂移", encoding="utf-8")

    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert result["snapshot"]["hash_drift_paths"] == [relative]


def test_invalid_evidence_date_blocks_snapshot(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["entries"][0]["source_date"] = "2026-1-11"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert "source_evidence_invalid" in result["blockers"]


def test_invalid_evidence_ref_rejects_source_from_candidate_snapshot(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["entries"][0]["evidence_ref"] = "garbage"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert "source_evidence_invalid" in result["blockers"]
    assert result["sources"] == []
    assert result["snapshot"]["rejected"] == [
        {
            "path": "docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md",
            "reason": "source_evidence_invalid",
        }
    ]


def test_preserves_complete_but_not_substitute_evidence_status(tmp_path):
    root, index, evidence = _inputs(tmp_path)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["entries"][0]["evidence_status"] = "complete_but_not_substitute"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = build_snapshot(index, root, evidence, tmp_path / "out")

    assert result["sources"][0]["evidence_status"] == "complete_but_not_substitute"

