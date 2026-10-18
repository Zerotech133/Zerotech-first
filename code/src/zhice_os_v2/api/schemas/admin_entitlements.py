"""管理员权益 API 的请求 schema（B1-7 查询 + B1-8 写入体）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AuditQueryParams(BaseModel):
    """审计分页查询参数（经 Depends 绑定到 query）。

    from_at/to_at 必须带时区，否则 field_validator 抛错 → FastAPI 返回 422，
    避免 naive datetime 被当作服务器本地时区静默误判。
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1)
    target_user_id: str | None = None
    actor_user_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    strategy_id: str | None = None
    from_at: datetime | None = None
    to_at: datetime | None = None

    @field_validator("from_at", "to_at")
    @classmethod
    def _require_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class SetMembershipBody(BaseModel):
    """设置/续费会员。mode=until 需带时区 valid_until；permanent 无 valid_until。

    reason/valid_until/version 的语义校验（invalid_reason/invalid_expiry/
    version_conflict）由服务层与路由错误映射统一处理，这里只做结构约束。
    """

    mode: Literal["until", "permanent"]
    valid_until: datetime | None = None
    reason: str
    version: int = Field(ge=0)


class SetSubscriptionsBody(BaseModel):
    strategy_ids: list[str] = Field(min_length=1)
    mode: Literal["until", "permanent"]
    valid_until: datetime | None = None
    reason: str
    versions: dict[str, int]


class RevokeSubscriptionsBody(BaseModel):
    strategy_ids: list[str] = Field(min_length=1)
    reason: str
    versions: dict[str, int]


class RevokeMembershipBody(BaseModel):
    """撤销会员（B2-4）。撤销同事务停用自建私有实例、取消排队开仓、释放预留。"""

    reason: str
    version: int = Field(ge=0)


class PaymentGrantsQueryParams(BaseModel):
    """支付发放列表查询（C-5，经 Depends 绑定 query）。时间过滤须带时区，否则 422。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1)
    user_id: str | None = None
    grant_status: str | None = None
    from_at: datetime | None = None
    to_at: datetime | None = None

    @field_validator("from_at", "to_at")
    @classmethod
    def _require_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class RetryGrantBody(BaseModel):
    """管理员手动重试发放（C-5）。reason 语义校验由路由 _clean_reason 统一处理。"""

    reason: str


class ReviewGrantBody(BaseModel):
    """历史歧义订单复核（C-5）。RETRY 转重试队列；MARK_GRANTED 对账标记不发权益。"""

    decision: Literal["RETRY", "MARK_GRANTED"]
    reason: str
