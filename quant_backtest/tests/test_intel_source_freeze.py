"""治理日冻结登记测试 + D2 关系候选规则将占位."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_source_freeze import (  # noqa: E402
    ALLOWED_ACTIVE_SOURCES, FROZEN_SOURCES, assert_source_active, freeze_snapshot,
)

REPO = Path(__file__).resolve().parents[3]


def test_frozen_sources_are_blocked_at_gate():
    for s in FROZEN_SOURCES:
        with pytest.raises(PermissionError, match="SOURCE_FROZEN"):
            assert_source_active(s)


def test_active_sources_pass_gate():
    for s in ALLOWED_ACTIVE_SOURCES:
        assert_source_active(s)  # 不抛即过


def test_snapshot_records_policy_and_honest_counts():
    snap = freeze_snapshot(REPO)
    assert snap["frozen_by"] == "老田" and len(snap["records"]) == 2
    for r in snap["records"]:
        assert r["frozen_at"] and r["basis"].startswith("K-MDC")
        assert r["stock_rows"] is None or isinstance(r["stock_rows"], int)  # unknown 如实
    assert "解冻须书面许可" in snap["policy"]
