"""策略聚合。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.strategy.spec import StrategySpec


class StrategyStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    STAGING = "STAGING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REJECTED = "REJECTED"


@dataclass
class Strategy:
    strategy_id: str
    strategy_name: str
    market: str
    strategy_type: str = "V2_EXTERNAL"
    status: StrategyStatus = StrategyStatus.DRAFT
    description: str = ""
    # 归属边界：owner_user_id=None 表示平台公共策略；否则为该用户的私有策略。
    owner_user_id: str | None = None
    # 可见性：PUBLIC=目录内所有人可见/可用；PRIVATE=仅 owner 可见/可编辑。
    visibility: str = "PUBLIC"
    # 发布态：PRIVATE/PENDING_REVIEW/PUBLISHED/REJECTED（私有策略走审核派生公共副本）。
    publication_status: str = "PUBLISHED"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class StrategyPublicationRequest:
    """私有策略发布申请：审核通过后派生一份平台公共策略/版本。"""

    request_id: str
    source_strategy_id: str
    requester_user_id: str
    status: str = "PENDING"  # PENDING/APPROVED/REJECTED
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    derived_strategy_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class StrategyVersion:
    strategy_version_id: str
    strategy_id: str
    version_no: str
    market: str
    spec: StrategySpec
    status: StrategyStatus = StrategyStatus.DRAFT
    research_ref: str | None = None
    created_at: datetime = field(default_factory=utc_now)
