"""交易计划。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class PlanStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class TradePlan:
    plan_id: str
    strategy_version_id: str
    market: str
    ts_code: str
    direction: str
    qty: Decimal
    ref_price: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    max_position_ratio: Decimal | None = Decimal("0.05")
    status: PlanStatus = PlanStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    # 运行时归属（Phase B 多用户执行隔离）。legacy 路径保持 None，不改变现有行为；
    # user_strategy_id -> v2_user_strategy_instance；account_id -> v2_user_account。
    user_strategy_id: str | None = None
    account_id: str | None = None
