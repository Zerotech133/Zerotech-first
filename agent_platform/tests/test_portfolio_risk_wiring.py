# -*- coding: utf-8 -*-
"""组合预算与风险授权边界测试。"""
import importlib.util
import json
from pathlib import Path

import pytest


PLATFORM = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM / "domains" / "portfolio_risk_wiring.py"
spec = importlib.util.spec_from_file_location("portfolio_risk_wiring_under_test", MODULE_PATH)
prw = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = prw
spec.loader.exec_module(prw)


BUDGET = prw.PortfolioBudgetDTO(
    portfolio_id="paper-main",
    max_gross_exposure=100000,
    max_single_position=10000,
    max_positions=10,
)
CONSTRAINTS = prw.RiskConstraintDTO(
    max_drawdown=0.1,
    stop_loss_pct=0.08,
    max_correlation=0.7,
    constraint_refs=("risk:v1",),
)


def _arbitration(**overrides):
    value = {
        "blocked": False,
        "consensus": "buy",
        "participating_strategy_ids": ["s1", "s2"],
        "consensus_recommendation": "buy",
        "evidence_refs": ["eval:001"],
        "trace_id": "trace-001",
    }
    value.update(overrides)
    return value


def test_shadow_consensus_is_review_only_and_never_executable():
    request = prw.build_authorization_request(
        _arbitration(), BUDGET, CONSTRAINTS,
        mode=prw.SHADOW_MODE,
        evidence_refs=("eval:001",),
        trace_id="trace-001",
        created_at="2026-10-18T00:00:00+00:00",
    )

    assert request.decision == prw.REVIEW
    assert request.execution_allowed is False
    assert prw.validate_authorization_request(request) is True
    assert request.audit_hash
    assert request.to_dict()["source_verdict"]["consensus"] == "buy"


def test_blocked_arbitration_defaults_to_deny():
    request = prw.build_authorization_request(
        _arbitration(blocked=True, consensus=None), BUDGET, CONSTRAINTS)

    assert request.decision == prw.DENY
    assert request.execution_allowed is False
    assert "blocked" in request.reason
    assert "缺少可授权共识" in request.reason


def test_missing_arbitration_fields_fail_closed():
    with pytest.raises(prw.PortfolioRiskWiringError, match="缺少字段"):
        prw.build_authorization_request(
            {"blocked": False, "consensus": "buy"}, BUDGET, CONSTRAINTS)


def test_invalid_budget_is_rejected():
    with pytest.raises(prw.PortfolioRiskWiringError, match="不能超过"):
        prw.PortfolioBudgetDTO(
            portfolio_id="p",
            max_gross_exposure=10,
            max_single_position=11,
        )


def test_tampered_audit_dto_is_rejected():
    request = prw.build_authorization_request(
        _arbitration(), BUDGET, CONSTRAINTS,
        created_at="2026-10-18T00:00:00+00:00")
    payload = request.to_dict()
    payload["reason"] = "被篡改"
    tampered = prw.PortfolioRiskAuthorizationRequest(
        request_id=payload["request_id"],
        mode=payload["mode"],
        decision=payload["decision"],
        execution_allowed=payload["execution_allowed"],
        portfolio_budget=BUDGET,
        risk_constraints=CONSTRAINTS,
        source_verdict=payload["source_verdict"],
        reason=payload["reason"],
        requested_by=payload["requested_by"],
        created_at=payload["created_at"],
        audit_hash=payload["audit_hash"],
        trace_id=payload["trace_id"],
        evidence_refs=tuple(payload["evidence_refs"]),
    )

    with pytest.raises(prw.PortfolioRiskWiringError, match="audit_hash"):
        prw.validate_authorization_request(tampered)


def test_boundary_rejects_allow_without_execution_and_allow_is_not_produced():
    with pytest.raises(prw.PortfolioRiskWiringError, match="ALLOW"):
        prw.PortfolioRiskAuthorizationRequest(
            request_id="r1",
            mode=prw.PAPER_MODE,
            decision=prw.ALLOW,
            execution_allowed=False,
            portfolio_budget=BUDGET,
            risk_constraints=CONSTRAINTS,
            source_verdict=_arbitration(),
            reason="试图越过授权边界",
            requested_by="test",
            created_at="2026-10-18T00:00:00+00:00",
            audit_hash="hash",
        )

    request = prw.build_authorization_request(
        _arbitration(), BUDGET, CONSTRAINTS, mode=prw.PAPER_MODE)
    assert request.decision == prw.REVIEW
    assert request.execution_allowed is False


def test_dto_is_json_serializable_without_execution_fields_hidden():
    request = prw.build_authorization_request(
        _arbitration(), BUDGET, CONSTRAINTS,
        created_at="2026-10-18T00:00:00+00:00")
    encoded = json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True)
    assert json.loads(encoded)["execution_allowed"] is False
