"""复盘案例。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    SKIPPED = "SKIPPED"


@dataclass
class ReplayCase:
    replay_id: str
    strategy_version_id: str
    market: str
    ts_code: str
    pnl: Decimal
    pnl_pct: Decimal
    holding_period_seconds: int
    exit_reason: str
    attribution: dict = field(default_factory=dict)
    status: ReviewStatus = ReviewStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    # 对齐列（V1 get_pnl_calendar / 晚报按 close_date 过滤依赖这些）
    open_date: date | None = None
    close_date: date | None = None
    qty: Decimal | None = None
    open_price: Decimal | None = None
    close_price: Decimal | None = None
    outcome: str | None = None
    # 运行时归属（Phase B 多用户执行隔离）。legacy 路径保持 None，不改变现有行为；
    # user_strategy_id -> v2_user_strategy_instance；account_id -> v2_user_account。
    user_strategy_id: str | None = None
    account_id: str | None = None
