# -*- coding: utf-8 -*-
"""策略结论到组合预算/风险授权请求的只读边界。

本模块只把 strategy_evaluation 的仲裁结果转换为可审计 DTO；不读取或修改
quant_backtest/portfolio.py，不写交易账本，也不调用任何执行接口。授权请求
默认拒绝，SHADOW 永远没有执行权。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Tuple


SHADOW_MODE = "SHADOW"
PAPER_MODE = "PAPER"
ALLOWED_MODES = frozenset((SHADOW_MODE, PAPER_MODE))

DENY = "DENY"
REVIEW = "REVIEW"
ALLOW = "ALLOW"
DECISIONS = frozenset((DENY, REVIEW, ALLOW))


class PortfolioRiskWiringError(ValueError):
    """组合预算/风险授权边界输入非法。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioRiskWiringError("%s 必须是非空字符串" % field_name)
    return value.strip()


def _finite_non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortfolioRiskWiringError("%s 必须是非负有限数字" % field_name)
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise PortfolioRiskWiringError("%s 必须是非负有限数字" % field_name)
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskWiringError("审计字段必须可规范化为 JSON") from exc


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PortfolioRiskWiringError("%s 必须是映射" % field_name)
    return value


def _audit_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PortfolioBudgetDTO:
    """只读组合预算 DTO；数值仅表达请求，不代表资金已分配。"""

    portfolio_id: str
    currency: str = "CNY"
    max_gross_exposure: float = 0.0
    max_single_position: float = 0.0
    max_positions: int = 0

    def __post_init__(self) -> None:
        _non_empty(self.portfolio_id, "portfolio_id")
        _non_empty(self.currency, "currency")
        for name in ("max_gross_exposure", "max_single_position"):
            _finite_non_negative(getattr(self, name), name)
        if (isinstance(self.max_positions, bool)
                or not isinstance(self.max_positions, int)
                or self.max_positions < 0):
            raise PortfolioRiskWiringError("max_positions 必须是非负整数")
        if self.max_single_position > self.max_gross_exposure:
            raise PortfolioRiskWiringError(
                "max_single_position 不能超过 max_gross_exposure")

    def to_dict(self) -> dict:
        return {
            "portfolio_id": self.portfolio_id,
            "currency": self.currency,
            "max_gross_exposure": self.max_gross_exposure,
            "max_single_position": self.max_single_position,
            "max_positions": self.max_positions,
        }


@dataclass(frozen=True)
class RiskConstraintDTO:
    """只读风险约束 DTO；缺字段不推断、不放宽。"""

    max_drawdown: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    max_correlation: Optional[float] = None
    constraint_refs: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("max_drawdown", "stop_loss_pct", "max_correlation"):
            value = getattr(self, name)
            if value is not None:
                _finite_non_negative(value, name)
        if self.max_correlation is not None and self.max_correlation > 1:
            raise PortfolioRiskWiringError("max_correlation 必须在 [0, 1] 内")
        if any(not isinstance(ref, str) or not ref.strip()
               for ref in self.constraint_refs):
            raise PortfolioRiskWiringError("constraint_refs 只能包含非空字符串")

    def to_dict(self) -> dict:
        return {
            "max_drawdown": self.max_drawdown,
            "stop_loss_pct": self.stop_loss_pct,
            "max_correlation": self.max_correlation,
            "constraint_refs": list(self.constraint_refs),
        }


@dataclass(frozen=True)
class PortfolioRiskAuthorizationRequest:
    """可审计授权请求；``decision`` 默认 DENY，``execution_allowed`` 恒 False。"""

    request_id: str
    mode: str
    decision: str
    execution_allowed: bool
    portfolio_budget: PortfolioBudgetDTO
    risk_constraints: RiskConstraintDTO
    source_verdict: dict
    reason: str
    requested_by: str
    created_at: str
    audit_hash: str
    trace_id: str = ""
    evidence_refs: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _non_empty(self.request_id, "request_id")
        if self.mode not in ALLOWED_MODES:
            raise PortfolioRiskWiringError("不支持的 mode=%s" % self.mode)
        if self.decision not in DECISIONS:
            raise PortfolioRiskWiringError("不支持的 decision=%s" % self.decision)
        if type(self.execution_allowed) is not bool:
            raise PortfolioRiskWiringError("execution_allowed 必须是 bool")
        if not isinstance(self.portfolio_budget, PortfolioBudgetDTO):
            raise PortfolioRiskWiringError("portfolio_budget 必须是 PortfolioBudgetDTO")
        if not isinstance(self.risk_constraints, RiskConstraintDTO):
            raise PortfolioRiskWiringError("risk_constraints 必须是 RiskConstraintDTO")
        if not isinstance(self.source_verdict, dict):
            raise PortfolioRiskWiringError("source_verdict 必须是 dict")
        if self.mode == SHADOW_MODE and self.execution_allowed:
            raise PortfolioRiskWiringError("SHADOW 模式禁止 execution_allowed=True")
        if not self.execution_allowed and self.decision == ALLOW:
            raise PortfolioRiskWiringError(
                "decision=ALLOW 必须显式具备执行权；默认边界不提供执行权")
        _non_empty(self.reason, "reason")
        _non_empty(self.requested_by, "requested_by")
        _non_empty(self.created_at, "created_at")
        _non_empty(self.audit_hash, "audit_hash")
        if any(not isinstance(ref, str) or not ref.strip()
               for ref in self.evidence_refs):
            raise PortfolioRiskWiringError("evidence_refs 只能包含非空字符串")

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "mode": self.mode,
            "decision": self.decision,
            "execution_allowed": self.execution_allowed,
            "portfolio_budget": self.portfolio_budget.to_dict(),
            "risk_constraints": self.risk_constraints.to_dict(),
            "source_verdict": self.source_verdict,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "created_at": self.created_at,
            "audit_hash": self.audit_hash,
            "trace_id": self.trace_id,
            "evidence_refs": list(self.evidence_refs),
        }


def _source_snapshot(arbitration: Mapping[str, Any]) -> dict:
    """提取并规范化仲裁结论，未知结构/缺关键字段一律拒绝。"""
    arbitration = _mapping(arbitration, "arbitration")
    required = ("blocked", "consensus", "participating_strategy_ids")
    missing = [field for field in required if field not in arbitration]
    if missing:
        raise PortfolioRiskWiringError("arbitration 缺少字段: %s" % missing)
    if type(arbitration["blocked"]) is not bool:
        raise PortfolioRiskWiringError("arbitration.blocked 必须是 bool")
    if arbitration["consensus"] not in (None, "buy", "sell", "hold"):
        raise PortfolioRiskWiringError("arbitration.consensus 非法")
    ids = arbitration["participating_strategy_ids"]
    if (not isinstance(ids, (list, tuple))
            or any(not isinstance(item, str) or not item.strip() for item in ids)):
        raise PortfolioRiskWiringError(
            "arbitration.participating_strategy_ids 必须是非空字符串序列")
    return json.loads(_canonical(dict(arbitration)))


def build_authorization_request(
    arbitration: Mapping[str, Any],
    portfolio_budget: PortfolioBudgetDTO,
    risk_constraints: RiskConstraintDTO,
    *,
    mode: str = SHADOW_MODE,
    requested_by: str = "strategy-boundary",
    request_id: str = "",
    trace_id: str = "",
    evidence_refs=(),
    created_at: Optional[str] = None,
) -> PortfolioRiskAuthorizationRequest:
    """把策略仲裁结论映射为请求 DTO；此函数没有执行路径。"""
    if mode not in ALLOWED_MODES:
        raise PortfolioRiskWiringError("不支持的 mode=%s" % mode)
    source = _source_snapshot(arbitration)
    request_id = _non_empty(request_id, "request_id") if request_id else hashlib.sha256(
        _canonical({"source": source, "mode": mode,
                    "portfolio_budget": portfolio_budget.to_dict(),
                    "risk_constraints": risk_constraints.to_dict()}).encode("utf-8")
    ).hexdigest()[:24]
    refs = tuple(evidence_refs)
    if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise PortfolioRiskWiringError("evidence_refs 只能包含非空字符串")

    reasons = []
    if source["blocked"]:
        reasons.append("策略仲裁 blocked")
    if source["consensus"] is None:
        reasons.append("策略结论缺少可授权共识")
    if not source["participating_strategy_ids"]:
        reasons.append("没有参与仲裁的策略")
    decision = DENY if reasons else REVIEW
    reason = "; ".join(reasons) if reasons else (
        "策略共识仅形成待人工复核授权请求；SHADOW 模式无执行权"
        if mode == SHADOW_MODE else "策略共识仅形成待人工复核授权请求")
    payload = {
        "request_id": request_id,
        "mode": mode,
        "decision": decision,
        "execution_allowed": False,
        "portfolio_budget": portfolio_budget.to_dict(),
        "risk_constraints": risk_constraints.to_dict(),
        "source_verdict": source,
        "reason": reason,
        "requested_by": requested_by,
        "created_at": created_at or _now_iso(),
        "trace_id": trace_id,
        "evidence_refs": list(refs),
    }
    return PortfolioRiskAuthorizationRequest(
        request_id=request_id,
        mode=mode,
        decision=decision,
        execution_allowed=False,
        portfolio_budget=portfolio_budget,
        risk_constraints=risk_constraints,
        source_verdict=source,
        reason=reason,
        requested_by=requested_by,
        created_at=payload["created_at"],
        trace_id=trace_id,
        evidence_refs=refs,
        audit_hash=_audit_hash(payload),
    )


def validate_authorization_request(request: PortfolioRiskAuthorizationRequest) -> bool:
    """fail-closed 校验：审计指纹、SHADOW 权限与决策一致性必须同时成立。"""
    if not isinstance(request, PortfolioRiskAuthorizationRequest):
        raise PortfolioRiskWiringError("request 必须是 PortfolioRiskAuthorizationRequest")
    payload = request.to_dict()
    audit_hash = payload.pop("audit_hash")
    if _audit_hash(payload) != audit_hash:
        raise PortfolioRiskWiringError("audit_hash 不匹配，拒绝授权请求")
    if request.mode == SHADOW_MODE and request.execution_allowed:
        raise PortfolioRiskWiringError("SHADOW 请求不得拥有执行权")
    if request.decision == ALLOW:
        raise PortfolioRiskWiringError("边界模块不接受 ALLOW 请求")
    if request.decision == DENY and not request.reason:
        raise PortfolioRiskWiringError("DENY 请求必须有 reason")
    return True


__all__ = [
    "ALLOW", "DENY", "REVIEW", "SHADOW_MODE", "PAPER_MODE",
    "PortfolioBudgetDTO", "RiskConstraintDTO",
    "PortfolioRiskAuthorizationRequest", "PortfolioRiskWiringError",
    "build_authorization_request", "validate_authorization_request",
]
