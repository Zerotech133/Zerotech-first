"""RuntimeContext：enforced 执行路径的归属贯穿载体（Phase B Task 5）。

一份不可变的运行时归属上下文，在 enforced 路径贯穿 plan→risk→approval→execution→
fill→position→replay，替代此前散落的裸字符串参数，保证归属 (user_id/
user_strategy_id/account_id/strategy_version_id) 与执行意图 (execution_mode/
auto_execute) 在整条链上一致传播。

安全铁律：legacy 路径**不构造**、**不使用** RuntimeContext——归属为 None 的计划/持仓
走原有裸调用，逐字回归一致。只有 enforced 路径（计划带 user_strategy_id/account_id）
才用它贯穿归属并触发预留 consume/release。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance


@dataclass(frozen=True)
class RuntimeContext:
    """enforced 执行链的运行时归属 + 执行意图（不可变）。"""

    user_id: str
    user_strategy_id: str
    account_id: str
    strategy_version_id: str
    execution_mode: str
    auto_execute: bool

    @classmethod
    def from_instance(
        cls,
        instance: UserStrategyInstance,
        *,
        execution_mode: str | None = None,
    ) -> RuntimeContext:
        """从用户策略运行实例构造上下文（权威归属取自实例）。"""
        return cls(
            user_id=instance.user_id,
            user_strategy_id=instance.user_strategy_id,
            account_id=instance.account_id,
            strategy_version_id=instance.strategy_version_id,
            execution_mode=execution_mode
            or str(getattr(instance.execution_mode, "value", instance.execution_mode)),
            auto_execute=bool(instance.auto_execute),
        )
