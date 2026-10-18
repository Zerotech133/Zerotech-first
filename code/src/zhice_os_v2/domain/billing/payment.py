"""支付订单领域模型（P3）：纯数据，仓储出边界用。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class PaymentOrder:
    order_id: str
    user_id: str
    product_type: str  # membership | subscription
    product_ref: str | None  # subscription: strategy_id；membership: None
    months: int
    amount_usdt: Decimal
    trade_type: str
    status: str  # pending | paid | expired | canceled
    bepusdt_trade_id: str | None = None
    pay_address: str | None = None
    actual_amount: Decimal | None = None
    expired_at: datetime | None = None
    paid_at: datetime | None = None
    granted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 单元 C 发放状态机（迁移 0031）
    grant_status: str = "pending"  # pending|processing|retrying|granted|failed|manual_review
    grant_attempts: int = 0
    automatic_grant_attempts: int = 0
    next_grant_retry_at: datetime | None = None
    last_grant_error_code: str | None = None
    last_grant_error_summary: str | None = None
    grant_lease_until: datetime | None = None
    grant_lease_owner: str | None = None
    historical_grant_ambiguous: bool = False
    grant_reviewed_at: datetime | None = None
    grant_reviewed_by_user_id: str | None = None
    grant_review_decision: str | None = None  # RETRY|MARK_GRANTED
    product_name_snapshot: str = ""
    market_snapshot: str | None = None
