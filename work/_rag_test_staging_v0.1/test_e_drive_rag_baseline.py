import hashlib
import json
from pathlib import Path

from run_e_drive_rag_baseline import run_baseline


def _source(root: Path, relative_path: str, text: str, source_id: str) -> dict:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    raw = path.read_bytes()
    return {
        "source_id": source_id,
        "path": str(path),
        "relative_path": relative_path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "license_scope": "pending",
        "raw_source_available": True,
    }


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "e"
    sources = [
        _source(
            root,
            "knowledge_docs/缠论技术分析知识库.md",
            "缠论三类买点：第一类买点、第二类买点、第三类买点。",
            "E-CHAN",
        ),
        _source(
            root,
            "docs/plans/2026-10-09_交易员训练与上岗体系v0.3.md",
            "交易员回答买什么、怎么买、买多少、何时卖。",
            "E-TRD",
        ),
        _source(
            root,
            "portal/strategies.json",
            '{"id":"ninedim","status":"live","name":"九维选股漏斗"}',
            "E-PORTAL",
        ),
    ]
    snapshot = tmp_path / "candidate-source-snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "academy_e_drive_candidate_snapshot/v1",
                "root": str(root),
                "training_eligible": False,
                "production_eligible": False,
                "blockers": [],
                "sources": sources,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return snapshot


def test_rag_has_e_drive_source_hit(tmp_path):
    report = run_baseline(_snapshot(tmp_path), tmp_path / "out")

    assert report["summary"]["source_recall_at_5"] == 1.0


def test_missing_evidence_refuses(tmp_path):
    report = run_baseline(_snapshot(tmp_path), tmp_path / "out")

    case = next(c for c in report["cases"] if c["case_id"] == "E-CORE-101-N01")
    assert case["decision"] == "refuse_missing_evidence"


def test_no_model_or_training_side_effects(tmp_path):
    report = run_baseline(_snapshot(tmp_path), tmp_path / "out")

    assert report["model_calls"] == 0
    assert report["training_steps"] == 0
    assert report["training_eligible"] is False
    assert report["production_eligible"] is False


def test_missing_expected_source_counts_as_recall_failure(tmp_path):
    snapshot_path = _snapshot(tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["sources"] = [
        source for source in payload["sources"] if source["source_id"] != "E-PORTAL"
    ]
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = run_baseline(snapshot_path, tmp_path / "out")

    assert report["summary"]["positive_total"] == 3
    assert report["summary"]["source_recall_at_5"] < 1.0


def test_snapshot_blocker_prevents_passing_gate(tmp_path):
    snapshot_path = _snapshot(tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["blockers"] = ["hash_drift"]
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = run_baseline(snapshot_path, tmp_path / "out")

    assert report["summary"]["blocked"] is True
    assert report["decision"] == "blocked_snapshot"


def test_snapshot_source_path_must_stay_under_declared_root(tmp_path):
    snapshot_path = _snapshot(tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    chan = next(source for source in payload["sources"] if source["source_id"] == "E-CHAN")
    outside = tmp_path / "outside.md"
    outside.write_text((tmp_path / "e/knowledge_docs/缠论技术分析知识库.md").read_text(encoding="utf-8"), encoding="utf-8")
    chan["path"] = str(outside)
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = run_baseline(snapshot_path, tmp_path / "out")

    assert any(issue.startswith("source_path_outside_root:") for issue in report["source_issues"])
    assert report["decision"] == "blocked_snapshot"


def test_source_recall_is_independent_of_term_coverage(tmp_path):
    snapshot_path = _snapshot(tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    portal = next(source for source in payload["sources"] if source["source_id"] == "E-PORTAL")
    portal_path = Path(portal["path"])
    portal_path.write_text('{"id":"ninedim"}', encoding="utf-8")
    raw = portal_path.read_bytes()
    portal["bytes"] = len(raw)
    portal["sha256"] = hashlib.sha256(raw).hexdigest()
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = run_baseline(snapshot_path, tmp_path / "out")

    portal_case = next(c for c in report["cases"] if c["case_id"] == "E-PORTAL-101-P01")
    assert portal_case["source_hit"] is True
    assert portal_case["term_coverage"] < 1.0
    assert report["summary"]["source_recall_at_5"] == 1.0

def test_temporal_baseline_surfaces_missing_source_dates(tmp_path):
    report = run_baseline(_snapshot(tmp_path), tmp_path / "out")

    assert report["summary"]["temporal_risk_cases"] >= 1
    assert report["summary"]["temporal_evidence_complete"] is False
    assert report["decision"] == "blocked_temporal_evidence"
