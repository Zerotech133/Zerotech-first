# -*- coding: utf-8 -*-
"""独立策略生命周期晋级门测试。"""
import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_promotion import (  # noqa: E402
    LEGAL_TRANSITIONS,
    StrategyPromotionError,
    StrategyPromotionGate,
    map_registry_state,
    promotion_ledger_path,
)


def _gate(tmp_path):
    return StrategyPromotionGate(tmp_path)


def _promote(gate, **overrides):
    values = dict(
        strategy_id="s1",
        version="1.2.0",
        from_state="APPROVED",
        to_state="ACTIVE",
        metrics={"sample_size": 120, "win_rate": 0.62,
                 "max_drawdown": 0.18},
        evidence_refs=["eval:run-001"],
        actor="reviewer",
        reason="评估通过",
        event_id="evt-001",
        thresholds={"min_sample_size": 100, "min_win_rate": 0.60,
                    "max_drawdown": 0.20},
        timestamp="2026-10-18T00:00:00+00:00",
    )
    values.update(overrides)
    return gate.promote(**values)


def test_registry_states_map_without_changing_registry():
    assert map_registry_state("experimental") == "SHADOW"
    assert map_registry_state("active") == "ACTIVE"
    assert map_registry_state("shadow") == "SHADOW"
    with pytest.raises(StrategyPromotionError, match="不支持"):
        map_registry_state("deprecated")


def test_legal_transitions_are_explicit():
    assert LEGAL_TRANSITIONS["DRAFT"] == frozenset(("EXPERIMENT",))
    assert LEGAL_TRANSITIONS["EXPERIMENT"] == frozenset(("SHADOW",))
    assert LEGAL_TRANSITIONS["SHADOW"] == frozenset(("APPROVED",))
    assert LEGAL_TRANSITIONS["APPROVED"] == frozenset(("ACTIVE", "RETIRED"))
    assert LEGAL_TRANSITIONS["ACTIVE"] == frozenset(("RETIRED",))
    assert LEGAL_TRANSITIONS["RETIRED"] == frozenset()


def test_valid_transition_writes_required_audit_event(tmp_path):
    gate = _gate(tmp_path)
    result = _promote(gate)

    assert result["status"] == "accepted"
    assert result["accepted"] is True
    assert result["from_state"] == "APPROVED"
    assert result["to_state"] == "ACTIVE"
    assert result["event"]["strategy_id"] == "s1"
    assert result["event"]["version"] == "1.2.0"
    assert result["event"]["evaluation_metrics"]["sample_size"] == 120
    assert result["event"]["evidence_refs"] == ["eval:run-001"]
    assert result["event"]["actor"] == "reviewer"
    assert result["event"]["reason"] == "评估通过"
    assert result["event"]["timestamp"] == "2026-10-18T00:00:00+00:00"
    assert len(gate.load_events()) == 1


def test_registry_states_map_before_transition_validation(tmp_path):
    result = _promote(_gate(tmp_path), from_state="experimental",
                      to_state="active")

    assert result["status"] == "rejected"
    assert result["from_state"] == "SHADOW"
    assert result["to_state"] == "ACTIVE"
    assert "非法生命周期迁移" in result["reason"]


def test_retired_can_follow_active_or_approved(tmp_path):
    for source in ("ACTIVE", "APPROVED"):
        result = _promote(_gate(tmp_path), from_state=source, to_state="RETIRED",
                          event_id="retire-" + source)
        assert result["accepted"] is True
        assert result["to_state"] == "RETIRED"


def test_illegal_transition_returns_rejection_and_does_not_write(tmp_path):
    gate = _gate(tmp_path)
    result = _promote(gate, from_state="DRAFT", to_state="ACTIVE")

    assert result["status"] == "rejected"
    assert result["accepted"] is False
    assert "非法生命周期迁移" in result["reason"]
    assert not gate.ledger_path.exists()


def test_active_gate_requires_evidence_metrics_and_thresholds(tmp_path):
    cases = (
        ({"evidence_refs": []}, "evidence_refs"),
        ({"metrics": {"sample_size": 120, "win_rate": 0.62}}, "max_drawdown"),
        ({"thresholds": {"min_sample_size": 100, "min_win_rate": 0.60}},
         "max_drawdown"),
        ({"metrics": {"sample_size": 50, "win_rate": 0.62,
                      "max_drawdown": 0.18}}, "sample_size"),
        ({"metrics": {"sample_size": 120, "win_rate": 0.40,
                      "max_drawdown": 0.18}}, "win_rate"),
        ({"metrics": {"sample_size": 120, "win_rate": 0.62,
                      "max_drawdown": 0.30}}, "max_drawdown"),
    )
    for index, (changes, expected) in enumerate(cases):
        gate = _gate(tmp_path / ("case-%d" % index))
        result = _promote(gate, event_id="gate-%d" % index, **changes)
        assert result["status"] == "rejected"
        assert result["accepted"] is False
        assert expected in result["reason"]
        assert not gate.ledger_path.exists()


def test_non_active_transition_does_not_require_active_thresholds(tmp_path):
    gate = _gate(tmp_path)
    result = _promote(gate, from_state="DRAFT", to_state="EXPERIMENT",
                       metrics={}, evidence_refs=[], event_id="draft-exp")

    assert result["accepted"] is True
    assert len(gate.load_events()) == 1


def test_event_id_is_idempotent_and_conflicting_replay_is_rejected(tmp_path):
    gate = _gate(tmp_path)
    first = _promote(gate)
    replay = _promote(gate)
    conflict = _promote(gate, reason="不同理由")

    assert first["status"] == "accepted"
    assert replay["status"] == "replayed"
    assert replay["replayed"] is True
    assert conflict["status"] == "rejected"
    assert "event_id 已存在" in conflict["reason"]
    assert len(gate.load_events()) == 1


def test_root_is_explicit_and_ledger_is_append_only_jsonl(tmp_path):
    with pytest.raises(StrategyPromotionError, match="显式提供"):
        promotion_ledger_path(None)
    with pytest.raises(StrategyPromotionError, match="显式提供"):
        StrategyPromotionGate("")

    gate = _gate(tmp_path)
    _promote(gate)
    _promote(gate, from_state="ACTIVE", to_state="RETIRED",
             event_id="evt-002", reason="退役")
    assert gate.ledger_path == tmp_path / "strategy" / "strategy_promotions.jsonl"
    rows = [json.loads(line) for line in gate.ledger_path.read_text(
        encoding="utf-8").splitlines()]
    assert [row["event_id"] for row in rows] == ["evt-001", "evt-002"]
