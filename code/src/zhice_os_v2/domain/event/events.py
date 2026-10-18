"""领域事件。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from zhice_os_v2.domain.ontology.objects import utc_now


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    occurred_at: datetime = field(default_factory=utc_now)
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    payload: dict = field(default_factory=dict)
    trace_id: str | None = None


@dataclass(frozen=True)
class PositionClosed(DomainEvent):
    market: str = ""
    strategy_version_id: int = 0
    ts_code: str = ""
    pnl: Decimal = Decimal("0")
    closed_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalRequested(DomainEvent):
    request_id: str = ""
    order_intent_id: str = ""
    market: str = ""
    strategy_version_id: int = 0
