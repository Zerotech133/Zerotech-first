"""风控决策。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class RiskResult(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass
class RiskDecision:
    risk_decision_id: str
    trade_plan_id: str
    result: RiskResult
    reasons: list[str] = field(default_factory=list)
    rule_results: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
