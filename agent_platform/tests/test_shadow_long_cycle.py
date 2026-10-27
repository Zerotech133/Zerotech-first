# -*- coding: utf-8 -*-
"""SHADOW 长周期只读聚合入口契约测试。"""

import json
from pathlib import Path
import sys

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from shadow_long_cycle import (  # noqa: E402
    ShadowLongCycleError,
    aggregate_shadow_evidence,
    event_id_for,
)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed(root):
    _write_jsonl(
        root / "data" / "shadow" / "shadow_ledger.jsonl",
        [
            {"status": "done", "run_id": "decision-1", "symbol": "600519"},
            {
                "status": "failed",
                "run_id": "decision-2",
                "symbol": "000858",
                "error": "source unavailable",
            },
        ],
    )
    _write_jsonl(
        root / "data" / "strategy" / "strategy_runs.jsonl",
        [
            {"status": "done", "run_id": "decision-1", "strategy_id": "s1"},
        ],
    )
    _write_jsonl(
        root / "strategy" / "strategy_promotions.jsonl",
        [
            {"status": "accepted", "event_id": "promotion-1"},
        ],
    )
    _write_jsonl(
        root / "data" / "perf_ledger.jsonl",
        [
            {"status": "recorded", "run_id": "decision-1", "ret_pct": 1.2},
        ],
    )


def test_default_is_read_only_and_missing_sources_are_visible(tmp_path):
    root = tmp_path / "root"
    _seed(root)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    result = aggregate_shadow_evidence(root, "cycle-1")

    assert result["status"] == "WARN"
    assert result["read_only"] is True
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert result["summary"]["shadow_done"] == 1
    assert result["summary"]["shadow_failed"] == 1
    assert any("文件缺失" in finding for finding in result["findings"])
    after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    assert after == before


def test_explicit_output_is_append_only_and_idempotent(tmp_path):
    root = tmp_path / "root"
    _seed(root)
    output = root / "evidence" / "long_cycle.jsonl"

    first = aggregate_shadow_evidence(root, "cycle-1", output_path=output)
    replay = aggregate_shadow_evidence(root, "cycle-1", output_path=output)

    assert first["persisted"] is True
    assert first["replayed"] is False
    assert replay["status"] == "REPLAYED"
    assert replay["replayed"] is True
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_same_run_with_changed_source_is_visible_failure(tmp_path):
    root = tmp_path / "root"
    _seed(root)
    output = root / "evidence.jsonl"
    aggregate_shadow_evidence(root, "cycle-1", output_path=output)

    shadow = root / "data" / "shadow" / "shadow_ledger.jsonl"
    shadow.write_text(
        shadow.read_text(encoding="utf-8")
        + json.dumps({"status": "done", "run_id": "decision-3"})
        + "\n",
        encoding="utf-8",
    )
    result = aggregate_shadow_evidence(root, "cycle-1", output_path=output)

    assert result["status"] == "FAILED"
    assert "run_id 已存在" in result["error"]
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_event_id_is_stable_and_output_must_be_inside_root(tmp_path):
    root = tmp_path / "root"
    _seed(root)
    result = aggregate_shadow_evidence(root, "cycle-1")
    assert result["event_id"] == event_id_for("cycle-1", result["sources"])

    with pytest.raises(ShadowLongCycleError, match="root 内"):
        aggregate_shadow_evidence(
            root, "cycle-1", output_path=tmp_path / "outside.jsonl"
        )


def test_bad_json_is_fail_visible(tmp_path):
    root = tmp_path / "root"
    _seed(root)
    shadow = root / "data" / "shadow" / "shadow_ledger.jsonl"
    shadow.write_text(
        shadow.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8"
    )

    result = aggregate_shadow_evidence(root, "cycle-1")

    assert result["status"] == "WARN"
    assert result["source_reports"]["shadow"]["invalid_records"] == 1
    assert any("shadow: 第 3 行非法 JSON" in finding for finding in result["findings"])
