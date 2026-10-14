import json

import pytest

from gap_release_audit import assert_publish_gates, audit_gap_release


def test_audit_blocks_when_receipt_missing(tmp_path):
    baseline = {"production_write": False, "counts": {"tdx_sector_daily": 1, "tdx_sector_minute": 0, "ts_margin_detail": 0, "listed_stock": 0, "total": 1}}
    result = audit_gap_release(tmp_path, baseline)
    assert result["publishable"] is False
    assert "missing_receipt" in result["blocked_reasons"]
    with pytest.raises(RuntimeError):
        assert_publish_gates(result)


def test_audit_requires_production_guard_and_no_pending_keys(tmp_path):
    (tmp_path / "receipt.json").write_text(json.dumps({"production_write": False, "global_activation": False, "counts": {"added": 1, "blocked": 0, "rejected": 0}, "rows": [{"status": "candidate_ready"}]}), encoding="utf-8")
    baseline = {"production_write": False, "counts": {"tdx_sector_daily": 1, "tdx_sector_minute": 0, "ts_margin_detail": 0, "listed_stock": 0, "total": 1}}
    result = audit_gap_release(tmp_path, baseline)
    assert result["publishable"] is True
    assert_publish_gates(result)


def test_audit_blocks_pending_keys(tmp_path):
    (tmp_path / "receipt.json").write_text(
        json.dumps({
            "production_write": False,
            "global_activation": False,
            "rows": [{"status": "pending"}],
        }),
        encoding="utf-8",
    )
    baseline = {"production_write": False, "counts": {"total": 1}}

    result = audit_gap_release(tmp_path, baseline)

    assert result["pending_count"] == 1
    assert result["publishable"] is False
    assert "pending_keys" in result["blocked_reasons"]

