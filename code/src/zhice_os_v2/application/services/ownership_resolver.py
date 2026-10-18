"""交易链运行时 owner 解析（Phase B Task 2，纯读）。

给定任意交易实体（计划/审批/意图/订单请求/执行/成交/持仓/复盘），解析出其归属的
用户策略运行实例，并以 ``v2_user_strategy_instance`` 为**权威**补齐
user_id / account_id / strategy_version_id。

解析链：
- plan / approval / intent / position / replay：**直接**读该行的 user_strategy_id
  （Phase B Task 1 已为这五表加 nullable 归属列）。
- request → intent：v2_order_request.intent_id → v2_order_intent.user_strategy_id。
- execution → request → intent：v2_execution_record.request_id → request → intent。
- fill → execution → request → intent：v2_fill_record.execution_id → … → intent。

安全铁律：
- 纯读，不改任何写入路径、不改 legacy 行为。
- legacy / 缺失 / 断链 / 实例查不到 → 返回 ``None``。
- **绝不**用 strategy_version_id 猜 owner——同一版本可被多用户共享，猜必错。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

# 直接携带归属列的实体：entity_type → (user_strategy_id 列, 主键列)
_DIRECT = {
    "plan": (orm.TradePlanModel.user_strategy_id, orm.TradePlanModel.plan_id),
    "approval": (
        orm.ApprovalRequestModel.user_strategy_id,
        orm.ApprovalRequestModel.request_id,
    ),
    "intent": (orm.OrderIntentModel.user_strategy_id, orm.OrderIntentModel.intent_id),
    "position": (orm.PositionModel.user_strategy_id, orm.PositionModel.position_id),
    "replay": (orm.ReplayModel.user_strategy_id, orm.ReplayModel.replay_id),
}

# 经订单链解析归属的实体
_CHAINED = {"request", "execution", "fill"}

_VALID_ENTITY_TYPES = frozenset(_DIRECT) | _CHAINED


@dataclass(frozen=True)
class RuntimeOwner:
    """交易实体的运行时归属（全部取自权威实例行）。"""

    user_id: str
    user_strategy_id: str
    account_id: str
    strategy_version_id: str


class OwnershipResolver:
    """把交易链上任意实体解析到其 :class:`RuntimeOwner`。

    注入一个返回 :class:`~sqlalchemy.orm.Session` 的 ``session_factory``（默认
    ``get_session``）；每次 ``resolve`` 打开一个只读会话，用完即关。
    """

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory: Callable[[], Session] = session_factory or get_session

    @contextmanager
    def _scope(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            yield db
        finally:
            db.close()

    def resolve(self, entity_type: str, entity_id: str) -> RuntimeOwner | None:
        """解析 owner；缺失 / legacy / 断链 / 实例查不到 → ``None``。"""
        if entity_type not in _VALID_ENTITY_TYPES:
            raise ValueError(f"unknown entity_type: {entity_type!r}")
        with self._scope() as db:
            user_strategy_id = self._resolve_user_strategy_id(db, entity_type, entity_id)
            if user_strategy_id is None:
                return None
            return self._owner_from_instance(db, user_strategy_id)

    # ---- 薄封装（计划测试用到 for_fill；其余便捷入口一并提供） ----
    def for_plan(self, plan_id: str) -> RuntimeOwner | None:
        return self.resolve("plan", plan_id)

    def for_approval(self, request_id: str) -> RuntimeOwner | None:
        return self.resolve("approval", request_id)

    def for_intent(self, intent_id: str) -> RuntimeOwner | None:
        return self.resolve("intent", intent_id)

    def for_request(self, request_id: str) -> RuntimeOwner | None:
        return self.resolve("request", request_id)

    def for_execution(self, execution_id: str) -> RuntimeOwner | None:
        return self.resolve("execution", execution_id)

    def for_fill(self, fill_id: str) -> RuntimeOwner | None:
        return self.resolve("fill", fill_id)

    def for_position(self, position_id: str) -> RuntimeOwner | None:
        return self.resolve("position", position_id)

    def for_replay(self, replay_id: str) -> RuntimeOwner | None:
        return self.resolve("replay", replay_id)

    # ---- 内部解析 ----
    def _resolve_user_strategy_id(
        self, db: Session, entity_type: str, entity_id: str
    ) -> str | None:
        """把任意实体解析到 user_strategy_id；任何缺失/断链/NULL → None。"""
        if entity_type in _DIRECT:
            owner_col, pk_col = _DIRECT[entity_type]
            return db.execute(
                select(owner_col).where(pk_col == entity_id)
            ).scalar_one_or_none()

        if entity_type == "request":
            intent_id = db.execute(
                select(orm.OrderRequestModel.intent_id).where(
                    orm.OrderRequestModel.request_id == entity_id
                )
            ).scalar_one_or_none()
            if intent_id is None:
                return None
            return self._resolve_user_strategy_id(db, "intent", intent_id)

        if entity_type == "execution":
            request_id = db.execute(
                select(orm.ExecutionRecordModel.request_id).where(
                    orm.ExecutionRecordModel.execution_id == entity_id
                )
            ).scalar_one_or_none()
            if request_id is None:
                return None
            return self._resolve_user_strategy_id(db, "request", request_id)

        if entity_type == "fill":
            execution_id = db.execute(
                select(orm.FillRecordModel.execution_id).where(
                    orm.FillRecordModel.fill_id == entity_id
                )
            ).scalar_one_or_none()
            if execution_id is None:
                return None
            return self._resolve_user_strategy_id(db, "execution", execution_id)

        # 不会到达：resolve 已在入口校验 entity_type。
        raise ValueError(f"unknown entity_type: {entity_type!r}")

    @staticmethod
    def _owner_from_instance(db: Session, user_strategy_id: str) -> RuntimeOwner | None:
        """以运行实例为权威构造 RuntimeOwner；实例查不到 → None。"""
        instance = db.execute(
            select(orm.UserStrategyInstanceModel).where(
                orm.UserStrategyInstanceModel.user_strategy_id == user_strategy_id
            )
        ).scalar_one_or_none()
        if instance is None:
            return None
        return RuntimeOwner(
            user_id=instance.user_id,
            user_strategy_id=instance.user_strategy_id,
            account_id=instance.account_id,
            strategy_version_id=instance.strategy_version_id,
        )
