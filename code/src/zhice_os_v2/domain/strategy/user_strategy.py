"""用户策略运行实例领域对象（v2_user_strategy_instance）。

区分 Strategy/StrategyVersion（共享定义、发布/治理状态）与 UserStrategyInstance
（某用户的实际运行边界：账户归属、参数、风控、自动执行开关、通知偏好）。
`Strategy/StrategyVersion.status` 只表示发布与治理；`enabled` 才表示是否实际运行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.accounts.user_account import UserAccount
from zhice_os_v2.domain.ontology.objects import utc_now


class ExecutionMode(StrEnum):
    """实例执行模式。

    - MANUAL：仅生成信号/计划，人工确认下单；
    - SEMI_AUTO：自动生成待审批，审批通过后执行；
    - FULL_AUTO：满足条件自动执行（仍受平台总开关/circuit breaker 约束）。
    """

    MANUAL = "MANUAL"
    SEMI_AUTO = "SEMI_AUTO"
    FULL_AUTO = "FULL_AUTO"


# 默认通知偏好：所有事件类型默认开启，用户可对不同实例分别关闭某类消息。
DEFAULT_NOTIFICATION_PREFERENCES: dict[str, bool] = {
    "signal": True,
    "plan": True,
    "approval": True,
    "risk": True,
    "order": True,
    "fill": True,
    "stop_loss": True,
    "take_profit": True,
    "runtime_error": True,
}


def _default_prefs() -> dict[str, bool]:
    return dict(DEFAULT_NOTIFICATION_PREFERENCES)


@dataclass
class UserStrategyInstance:
    user_strategy_id: str
    user_id: str
    strategy_version_id: str
    account_id: str
    enabled: bool = False
    auto_execute: bool = False
    execution_mode: ExecutionMode = ExecutionMode.MANUAL
    capital_quota: Decimal | None = None
    max_position_ratio: Decimal = Decimal("0.1")
    risk_parameters: dict = field(default_factory=dict)
    strategy_parameters: dict = field(default_factory=dict)
    notification_preferences: dict = field(default_factory=_default_prefs)
    upgrade_available_version_id: str | None = None
    pending_upgrade_version_id: str | None = None
    upgrade_requested_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    # 非持久化：绑定账户的便捷引用（由 service 载入时挂上，仓储不负责填充）。
    account: UserAccount | None = None
