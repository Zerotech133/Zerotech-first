"""管理员权益操作与审计：纯领域类型（仓储/服务出边界用）。

- AdminOperation：请求级幂等记录（actor+key 唯一，存首次响应）；
- EntitlementAudit：逐资源审计事件（一次批量 → 多条）；
- AdminWriteContext/Result：服务写操作的输入上下文与幂等结果；
- AuditQuery：审计分页查询条件。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class AdminOperation:
    operation_id: str
    actor_user_id: str
    idempotency_key: str
    request_fingerprint: str
    action: str
    target_user_id: str
    response_status: int
    response_body: dict
    created_at: datetime


@dataclass(frozen=True)
class EntitlementAudit:
    audit_id: str
    operation_id: str | None
    actor_user_id: str | None
    target_user_id: str
    source_type: Literal["ADMIN", "PAYMENT"]
    action: str
    resource_type: Literal["ROLE", "MEMBERSHIP", "SUBSCRIPTION", "PAYMENT_GRANT"]
    resource_id: str | None
    reason: str
    before_state: dict
    after_state: dict
    source_order_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class AdminWriteContext:
    """一次管理员写请求的幂等上下文（actor + 规范化 key/指纹）。"""

    actor_user_id: str
    idempotency_key: str
    request_fingerprint: str


@dataclass(frozen=True)
class AdminWriteResult:
    """写操作结果：HTTP 状态 + 响应体；replayed=True 表示命中幂等复放。"""

    status_code: int
    body: dict
    replayed: bool = False


@dataclass(frozen=True)
class AuditQuery:
    page: int = 1
    page_size: int = 50
    target_user_id: str | None = None
    actor_user_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    strategy_id: str | None = None
    from_at: datetime | None = None
    to_at: datetime | None = None
