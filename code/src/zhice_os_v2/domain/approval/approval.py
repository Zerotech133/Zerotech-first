"""交易授权领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    # 超持仓卖出：部分已平仓入账，但有剩余无法平仓；已平部分不回滚，需人工核查
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"


class ApprovalChannel(StrEnum):
    WEB_POPUP = "WEB_POPUP"
    IM_WECHAT = "IM_WECHAT"
    IM_QQ = "IM_QQ"
    IM_FEISHU = "IM_FEISHU"
    AUTO = "AUTO"


@dataclass
class ApprovalRequest:
    request_id: str
    order_intent_id: str
    strategy_version_id: str
    market: str
    ts_code: str
    direction: str
    qty: float
    price: float | None
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    channel: ApprovalChannel = ApprovalChannel.WEB_POPUP
    token: str = ""
    auto_follow: bool = False
    created_at: datetime = field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str | None = None
    reject_reason: str | None = None
    # 运行时归属（Phase B 多用户执行隔离）。legacy 路径保持 None，不改变现有行为；
    # user_strategy_id -> v2_user_strategy_instance；account_id -> v2_user_account。
    user_strategy_id: str | None = None
    account_id: str | None = None

    def approve(self, actor: str = "system") -> None:
        if self.status != ApprovalStatus.PENDING:
            raise ValueError(f"cannot approve request in status {self.status}")
        self.status = ApprovalStatus.APPROVED
        self.decided_at = utc_now()
        self.decided_by = actor

    def reject(self, actor: str, reason: str) -> None:
        if self.status != ApprovalStatus.PENDING:
            raise ValueError(f"cannot reject request in status {self.status}")
        self.status = ApprovalStatus.REJECTED
        self.decided_at = utc_now()
        self.decided_by = actor
        self.reject_reason = reason

    def expire(self) -> None:
        if self.status == ApprovalStatus.PENDING:
            self.status = ApprovalStatus.EXPIRED
            self.decided_at = utc_now()
