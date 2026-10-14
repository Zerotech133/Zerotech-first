# -*- coding: utf-8 -*-
"""血缘清单的来源、指纹和发布后回读门禁。"""
from __future__ import annotations

import hashlib

import pytest

from lineage_manifest import build_lineage_manifest


def _payload(tmp_path):
    staged = tmp_path / "staged.jsonl"
    target = tmp_path / "published.jsonl"
    staged.write_text('{"ts_code":"000001.SZ"}\n', encoding="utf-8")
    target.write_bytes(staged.read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt = {"receipt_id": "r1", "table": "ts_daily_basic", "unit_id": "u1", "result_rows": 1}
    complete = {"commit_id": "c1", "unit_id": "u1", "table": "ts_daily_basic", "receipt_ids": ["r1"],
                "files": [{"target": str(target), "new_sha256": digest, "counts": {"processed_rows": 1}}]}
    readback = {"sha256": digest, "rows": 1, "unique_keys": 1, "query_digest": "q1"}
    return staged, target, receipt, complete, readback


def test_lineage_manifest_rejects_missing_receipt(tmp_path):
    staged, target, _receipt, complete, readback = _payload(tmp_path)
    with pytest.raises(ValueError, match="来源回执"):
        build_lineage_manifest("u1", source_receipts=[], commit_complete=complete,
                               staging_path=staged, target=target, primary_key=["ts_code"],
                               readback=readback)


def test_lineage_manifest_rejects_fingerprint_or_readback_drift(tmp_path):
    staged, target, receipt, complete, readback = _payload(tmp_path)
    readback["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="指纹"):
        build_lineage_manifest("u1", source_receipts=[receipt], commit_complete=complete,
                               staging_path=staged, target=target, primary_key=["ts_code"],
                               readback=readback)


def test_lineage_manifest_contains_source_and_readback_digest(tmp_path):
    staged, target, receipt, complete, readback = _payload(tmp_path)
    result = build_lineage_manifest("u1", source_receipts=[receipt], commit_complete=complete,
                                    staging_path=staged, target=target, primary_key=["ts_code"],
                                    readback=readback)
    assert result["unit_id"] == "u1"
    assert result["source_receipt_ids"] == ["r1"]
    assert result["readback_digest"] == "q1"
    assert result["production_write"] is False
