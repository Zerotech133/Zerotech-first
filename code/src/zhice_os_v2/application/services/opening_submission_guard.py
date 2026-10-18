"""单元 B Task B2-2：开仓提交门禁（锁定快照，供 broker 提交时线性化）。

`hold()` 开自己的短事务，按**锁序 user→membership→instance**（与 B2-1 撤销事务一致，
防死锁）加 FOR UPDATE 锁住相关行，判定该开仓是否放行，然后 yield 一个只读快照，
**持锁不提交**直到调用方的 with 块退出——broker 在此窗口内提交订单即与撤销事务
线性化：撤销必须等本锁释放，本门禁读到的是权威（已提交的最新）会员/实例状态。

判定：
- 实例不存在                → opening.instance_not_found
- instance.enabled False     → opening.instance_disabled
- 自建私有(非平台) 且会员无效 → opening.membership_required（domain 谓词判定）
- 平台策略(owner=None 或 PUBLIC) → 免会员（enabled 仍须 True）

会员有效性一律复用 domain 谓词 is_member_tier_active（撤销/过期/无行→False），
不复制判定逻辑；membership 行 → 领域对象复用仓储 _to_membership（不复制映射）。

SQLite 下 with_for_update 为 no-op，隔离单测只验判定逻辑；真正的进程级线性化
由 B2-4 real_pg 用例覆盖。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from zhice_os_v2.domain.billing.entitlement import is_member_tier_active
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.membership_repository import _to_membership


class OpeningSubmissionDenied(RuntimeError):
    """开仓提交被门禁拒绝，code 为稳定机读原因串（opening.*）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OpeningSubmissionSnapshot:
    user_strategy_id: str
    user_id: str
    account_id: str | None
    is_platform_strategy: bool
    membership_active: bool


class OpeningSubmissionGuard:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory or get_session
        self._clock = clock

    @contextmanager
    def hold(self, *, user_strategy_id: str) -> Iterator[OpeningSubmissionSnapshot]:
        session = self._session_factory()
        try:
            snapshot = self._resolve_locked(session, user_strategy_id)
            yield snapshot
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _resolve_locked(
        self, session: Session, user_strategy_id: str
    ) -> OpeningSubmissionSnapshot:
        now = self._clock()

        # 1) 无锁读取 owner user_id（拿不到即实例不存在）。
        owner_user_id = (
            session.query(orm.UserStrategyInstanceModel.user_id)
            .filter_by(user_strategy_id=user_strategy_id)
            .scalar()
        )
        if owner_user_id is None:
            raise OpeningSubmissionDenied("opening.instance_not_found")

        # 2) 锁序 user→membership→instance（与 B2-1 撤销事务一致，防死锁）。
        session.query(orm.UserModel).filter_by(
            user_id=owner_user_id
        ).with_for_update().first()

        membership_row = (
            session.query(orm.MembershipModel)
            .filter_by(user_id=owner_user_id)
            .with_for_update()
            .first()
        )

        # 3) 重新 FOR UPDATE 读实例取权威 enabled（并发撤销会在此点线性化）。
        instance = (
            session.query(orm.UserStrategyInstanceModel)
            .filter_by(user_strategy_id=user_strategy_id)
            .with_for_update()
            .first()
        )
        if instance is None:  # 极端并发下实例被删：与"不存在"同语义。
            raise OpeningSubmissionDenied("opening.instance_not_found")

        # 4) 解析底层策略归属/可见性 → 是否平台策略。
        owner_of_strategy, visibility = (
            session.query(
                orm.StrategyModel.owner_user_id, orm.StrategyModel.visibility
            )
            .join(
                orm.StrategyVersionModel,
                orm.StrategyVersionModel.strategy_id == orm.StrategyModel.strategy_id,
            )
            .filter(
                orm.StrategyVersionModel.strategy_version_id
                == instance.strategy_version_id
            )
            .one()
        )
        is_platform_strategy = owner_of_strategy is None or visibility == "PUBLIC"

        # 5) 会员有效性复用 domain 谓词（无行→None→False）。
        membership = _to_membership(membership_row) if membership_row else None
        membership_active = is_member_tier_active(membership, now)

        # 6) 判定。
        if not instance.enabled:
            raise OpeningSubmissionDenied("opening.instance_disabled")
        if not is_platform_strategy and not membership_active:
            raise OpeningSubmissionDenied("opening.membership_required")

        return OpeningSubmissionSnapshot(
            user_strategy_id=instance.user_strategy_id,
            user_id=instance.user_id,
            account_id=instance.account_id,
            is_platform_strategy=is_platform_strategy,
            membership_active=membership_active,
        )
