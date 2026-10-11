from run_e_drive_rag_baseline import classify_course, evaluate_temporal_integrity


def test_course_classification_covers_core_strategy_risk_tool():
    assert classify_course({"relative_path": "knowledge_docs/缠论技术分析知识库.md"}) == "strategy"
    assert classify_course({"relative_path": "knowledge_docs/AI工程实践知识库.md"}) == "core"
    assert classify_course({"relative_path": "docs/risk/risk.md"}) == "risk"
    assert classify_course({"relative_path": "tools/run.py"}) == "tool"


def test_temporal_integrity_rejects_late_evidence():
    result = evaluate_temporal_integrity(
        {"as_of": "2026-01-31"}, [{"source_date": "2026-02-01"}]
    )
    assert result["temporal_leak"] is True


def test_temporal_integrity_requires_dates():
    result = evaluate_temporal_integrity({"as_of": "2026-01-31"}, [{}])
    assert result["status"] == "missing_evidence"

def test_temporal_integrity_rejects_invalid_iso_dates():
    result = evaluate_temporal_integrity(
        {"as_of": "2026-1-31"}, [{"source_date": "2026-02-01"}]
    )
    assert result["status"] == "invalid_evidence"
    assert result["invalid_dates"] == 1
