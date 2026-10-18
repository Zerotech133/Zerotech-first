"""UserStrategyRepository: v2_user_strategy_instance 表的 owner-scoped 持久化。

与 user_account_repository.py 同范式：每方法自开 session、try/finally close，需要行锁用
with_for_update()。owner-scoped 查询强制带 user_id 边界（get_owned 非 owner 返回 None，
不泄露存在性）。市场过滤经 join v2_user_account（实例本身不存 market）。
"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from zhice_os_v2.domain.accounts.user_account import UserAccount
from zhice_os_v2.domain.strategy.user_strategy import (
    ExecutionMode,
    UserStrategyInstance,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.user_account_repository import (
    _to_domain as _account_to_domain,
)


def _to_domain(row: orm.UserStrategyInstanceModel) -> UserStrategyInstance:
    return UserStrategyInstance(
        user_strategy_id=row.user_strategy_id,
        user_id=row.user_id,
        strategy_version_id=row.strategy_version_id,
        account_id=row.account_id,
        enabled=bool(row.enabled),
        auto_execute=bool(row.auto_execute),
        execution_mode=ExecutionMode(row.execution_mode),
        capital_quota=(
            None if row.capital_quota is None else Decimal(str(row.capital_quota))
        ),
        max_position_ratio=Decimal(str(row.max_position_ratio)),
        risk_parameters=dict(row.risk_parameters or {}),
        strategy_parameters=dict(row.strategy_parameters or {}),
        notification_preferences=dict(row.notification_preferences or {}),
        upgrade_available_version_id=row.upgrade_available_version_id,
        pending_upgrade_version_id=row.pending_upgrade_version_id,
        upgrade_requested_at=row.upgrade_requested_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class UserStrategyRepository:
    def _s(self):
        return get_session()

    def create(self, inst: UserStrategyInstance) -> UserStrategyInstance:
        s = self._s()
        try:
            row = orm.UserStrategyInstanceModel(
                user_strategy_id=inst.user_strategy_id,
                user_id=inst.user_id,
                strategy_version_id=inst.strategy_version_id,
                account_id=inst.account_id,
                enabled=inst.enabled,
                auto_execute=inst.auto_execute,
                execution_mode=str(inst.execution_mode),
                capital_quota=inst.capital_quota,
                max_position_ratio=inst.max_position_ratio,
                risk_parameters=dict(inst.risk_parameters),
                strategy_parameters=dict(inst.strategy_parameters),
                notification_preferences=dict(inst.notification_preferences),
                upgrade_available_version_id=inst.upgrade_available_version_id,
                pending_upgrade_version_id=inst.pending_upgrade_version_id,
                upgrade_requested_at=inst.upgrade_requested_at,
                created_at=inst.created_at,
                updated_at=inst.updated_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def update(self, inst: UserStrategyInstance) -> UserStrategyInstance:
        """按 user_strategy_id 全量回写可变字段。"""
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=inst.user_strategy_id)
                .first()
            )
            if row is None:
                raise LookupError("user_strategy.not_found")
            row.enabled = inst.enabled
            row.auto_execute = inst.auto_execute
            row.execution_mode = str(inst.execution_mode)
            row.capital_quota = inst.capital_quota
            row.max_position_ratio = inst.max_position_ratio
            row.risk_parameters = dict(inst.risk_parameters)
            row.strategy_parameters = dict(inst.strategy_parameters)
            row.notification_preferences = dict(inst.notification_preferences)
            row.upgrade_available_version_id = inst.upgrade_available_version_id
            row.pending_upgrade_version_id = inst.pending_upgrade_version_id
            row.upgrade_requested_at = inst.upgrade_requested_at
            row.updated_at = inst.updated_at
            s.commit()
            s.refresh(row)
            return _to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def enable_with_checks(
        self,
        user_strategy_id: str,
        user_id: str,
        validate: Callable[
            [UserStrategyInstance, UserAccount, list[UserStrategyInstance]], None
        ],
    ) -> UserStrategyInstance | None:
        """单事务串行化启用（修 I1/I2）。

        在同一 session 内：(1) owner 边界加载实例；(2) with_for_update 锁住其所属
        v2_user_account 行作串行化点；(3) 同 session 查该账户下所有 enabled 实例；
        (4) 调用校验回调（专用账户单实例 / 共享配额和 / 重复同版本 / 账户 ACTIVE /
        版本可见），失败抛 ValueError（或 LookupError）；(5) 通过则 enabled=True、
        刷新 updated_at、commit。异常 rollback 再抛。

        实例不存在/非 owner 返回 None（服务映射 LookupError，不泄露存在性）；已启用则
        幂等返回。SQLite 下 with_for_update 为 no-op 但逻辑仍在单事务内串行，可接受。
        部分唯一索引冲突（并发绕过预检）经 IntegrityError → ValueError 兜底（修 I3）。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id, user_id=user_id)
                .first()
            )
            if row is None:
                return None
            if row.enabled:
                return _to_domain(row)

            account_row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=row.account_id)
                .with_for_update()
                .first()
            )
            if account_row is None:
                raise LookupError("user_strategy.account_not_found")

            others_rows = (
                s.query(orm.UserStrategyInstanceModel)
                .filter(
                    orm.UserStrategyInstanceModel.account_id == row.account_id,
                    orm.UserStrategyInstanceModel.enabled.is_(True),
                )
                .all()
            )

            inst = _to_domain(row)
            account = _account_to_domain(account_row)
            others = [_to_domain(r) for r in others_rows]
            validate(inst, account, others)

            row.enabled = True
            row.updated_at = datetime.now(UTC)
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                raise ValueError("user_strategy.duplicate_active_version") from exc
            s.refresh(row)
            return _to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def get_owned(
        self, user_strategy_id: str, user_id: str, *, for_update: bool = False
    ) -> UserStrategyInstance | None:
        s = self._s()
        try:
            q = s.query(orm.UserStrategyInstanceModel).filter_by(
                user_strategy_id=user_strategy_id, user_id=user_id
            )
            if for_update:
                q = q.with_for_update()
            row = q.first()
            return _to_domain(row) if row is not None else None
        finally:
            s.close()

    def get_by_id(self, user_strategy_id: str) -> UserStrategyInstance | None:
        """按 user_strategy_id 载入实例（无 owner 边界）。

        仅供**内部路由**使用（如 should_notify 的通知事件判定——事件归属已由
        user_strategy_id 定死，无用户输入面）；一切外部 API 仍须走 get_owned。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id)
                .first()
            )
            return _to_domain(row) if row is not None else None
        finally:
            s.close()

    def list_by_user(
        self,
        user_id: str,
        market: str | None = None,
        enabled: bool | None = None,
    ) -> list[UserStrategyInstance]:
        s = self._s()
        try:
            q = s.query(orm.UserStrategyInstanceModel).filter(
                orm.UserStrategyInstanceModel.user_id == user_id
            )
            if enabled is not None:
                q = q.filter(orm.UserStrategyInstanceModel.enabled == enabled)
            if market is not None:
                q = q.join(
                    orm.UserAccountModel,
                    orm.UserAccountModel.account_id
                    == orm.UserStrategyInstanceModel.account_id,
                ).filter(orm.UserAccountModel.market == market)
            rows = q.order_by(
                orm.UserStrategyInstanceModel.created_at,
                orm.UserStrategyInstanceModel.id,
            ).all()
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_all(self) -> list[UserStrategyInstance]:
        """全部用户运行实例（无 owner 边界；仅供 ADMIN 全局查看 API 使用）。"""
        s = self._s()
        try:
            rows = (
                s.query(orm.UserStrategyInstanceModel)
                .order_by(
                    orm.UserStrategyInstanceModel.created_at,
                    orm.UserStrategyInstanceModel.id,
                )
                .all()
            )
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_enabled_by_account(self, account_id: str) -> list[UserStrategyInstance]:
        """某账户下所有启用实例（配额/单实例校验用；owner 已在服务层建立）。"""
        s = self._s()
        try:
            rows = (
                s.query(orm.UserStrategyInstanceModel)
                .filter(
                    orm.UserStrategyInstanceModel.account_id == account_id,
                    orm.UserStrategyInstanceModel.enabled.is_(True),
                )
                .all()
            )
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_enabled_for_market(self, market: str) -> list[UserStrategyInstance]:
        """某市场所有启用实例（全局，无 user 边界；供调度使用）。"""
        s = self._s()
        try:
            rows = (
                s.query(orm.UserStrategyInstanceModel)
                .join(
                    orm.UserAccountModel,
                    orm.UserAccountModel.account_id
                    == orm.UserStrategyInstanceModel.account_id,
                )
                .filter(
                    orm.UserAccountModel.market == market,
                    orm.UserStrategyInstanceModel.enabled.is_(True),
                )
                .order_by(
                    orm.UserStrategyInstanceModel.created_at,
                    orm.UserStrategyInstanceModel.id,
                )
                .all()
            )
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_pending_upgrade_instances(
        self, market: str | None = None
    ) -> list[UserStrategyInstance]:
        """pending_upgrade_version_id 非空的实例（升级自动切换执行器消费）。

        不过滤 enabled：disabled 实例的升级意图同样应被执行（横幅承诺不分启停）。
        market 经 join v2_user_account 过滤（实例本身不存 market）。
        """
        s = self._s()
        try:
            q = s.query(orm.UserStrategyInstanceModel).filter(
                orm.UserStrategyInstanceModel.pending_upgrade_version_id.isnot(None)
            )
            if market is not None:
                q = q.join(
                    orm.UserAccountModel,
                    orm.UserAccountModel.account_id
                    == orm.UserStrategyInstanceModel.account_id,
                ).filter(orm.UserAccountModel.market == market)
            rows = q.order_by(
                orm.UserStrategyInstanceModel.created_at,
                orm.UserStrategyInstanceModel.id,
            ).all()
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_bound_by_versions(
        self, version_ids: list[str] | set[str]
    ) -> list[UserStrategyInstance]:
        """绑定在给定策略版本集合上的全部实例（策略删除死锁检查用，不分启停）。

        v2_user_strategy_instance.strategy_version_id 对 v2_strategy_version 有 FK
        且无级联——任何绑定实例（enabled 或 disabled）都会阻塞策略/版本删除，
        调用方须先以 409 引导用户删实例，而不是让 DB 层 IntegrityError 炸 500。
        """
        ids = list(version_ids)
        if not ids:
            return []
        s = self._s()
        try:
            rows = (
                s.query(orm.UserStrategyInstanceModel)
                .filter(orm.UserStrategyInstanceModel.strategy_version_id.in_(ids))
                .all()
            )
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def apply_upgrade(
        self, user_strategy_id: str, expected_target_id: str
    ) -> UserStrategyInstance | None:
        """单事务原子切换：strategy_version_id → pending 目标、清 pending 三元组、
        落一条 v2_access_audit（action=strategy_upgrade_applied，actor=实例 owner）。

        with_for_update 锁实例行；锁内复核 pending 仍等于 expected_target_id 才动
        （并发/重入路径不一致即放弃返回 None——幂等）。SQLite 下 for_update 为
        no-op，但逻辑仍在单事务内。
        """
        now = datetime.now(UTC)
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return None
            if row.pending_upgrade_version_id != expected_target_id:
                return None
            from_version = row.strategy_version_id
            row.strategy_version_id = expected_target_id
            row.pending_upgrade_version_id = None
            row.upgrade_requested_at = None
            row.updated_at = now
            s.add(
                orm.AccessAuditModel(
                    audit_id=f"audit-{user_strategy_id}-{secrets.token_hex(6)}",
                    actor_user_id=row.user_id,
                    action="strategy_upgrade_applied",
                    resource_type="user_strategy_instance",
                    resource_id=user_strategy_id,
                    request_id=f"upgrade-{from_version}-{expected_target_id}",
                    created_at=now,
                )
            )
            s.commit()
            s.refresh(row)
            return _to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def clear_pending_upgrade(
        self, user_strategy_id: str, expected_target_id: str
    ) -> bool:
        """清 pending 升级意图（目标版本缺失/不可用时的兜底，不留死 pending）。

        锁内复核 pending == expected_target_id 才清（幂等；不一致返回 False）。
        """
        now = datetime.now(UTC)
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id)
                .with_for_update()
                .first()
            )
            if row is None or row.pending_upgrade_version_id != expected_target_id:
                return False
            row.pending_upgrade_version_id = None
            row.upgrade_requested_at = None
            row.updated_at = now
            s.commit()
            return True
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def delete_owned_with_checks(
        self, user_strategy_id: str, user_id: str
    ) -> dict | None:
        """单事务删除本人实例 + 释放其资金预留 +（专用账户且无他引用时）连带删账户。

        前置校验（ValueError 形态 code 由服务层/路由翻译）：
        - 实例不存在/非 owner → None（not-found 等价，不泄露存在性）；
        - enabled=True → ValueError("instance.not_disabled")（活跃实例先停用）。

        级联口径（FK 无级联，须显式清）：
        - v2_account_reservation：RESERVED 预留先"释放"（账户 reserved_cash 等额
          回落，clamp 0 保不变量），随后删除该实例全部台账行（CONSUMED/RELEASED
          历史行同样 FK 阻塞实例删除；cash_balance 不动——已实扣的不回滚，删
          实例语义=清运行痕迹而非退款）；
        - v2_notification_event.user_strategy_id 置 NULL（事件历史保留，转全局
          归属形态——该列 nullable 本就支持）；
        - 账户删除仅限 STRATEGY_DEDICATED 且无其他实例引用且 reserved_cash==0
          （SHARED_PORTFOLIO 是用户资产，生命周期归 /my/accounts 面；删账户前
          同步置 NULL 其 notification_event.account_id 引用）。

        返回 {"deleted": True, "account_id": 被删账户 id | None}。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id, user_id=user_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return None
            if row.enabled:
                raise ValueError("instance.not_disabled")

            account_id = row.account_id
            account_row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=account_id)
                .with_for_update()
                .first()
            )

            # 1) 释放该实例 RESERVED 预留（等额回落，clamp 0 保不变量）。
            reserved_rows = (
                s.query(orm.AccountReservationModel)
                .filter_by(user_strategy_id=user_strategy_id)
                .all()
            )
            if account_row is not None:
                reserved_sum = sum(
                    (
                        Decimal(str(r.amount))
                        for r in reserved_rows
                        if r.status == "RESERVED"
                    ),
                    Decimal("0"),
                )
                new_reserved = Decimal(str(account_row.reserved_cash)) - reserved_sum
                if new_reserved < 0:
                    new_reserved = Decimal("0")
                account_row.reserved_cash = new_reserved
                account_row.updated_at = datetime.now(UTC)

            # 2) 删该实例全部台账行（FK 阻塞实例删除，须先清）。
            for r in reserved_rows:
                s.delete(r)

            # 3) 事件归属置 NULL（保留事件历史，解除 FK）。
            s.query(orm.NotificationEventModel).filter(
                orm.NotificationEventModel.user_strategy_id == user_strategy_id
            ).update(
                {orm.NotificationEventModel.user_strategy_id: None},
                synchronize_session=False,
            )

            # 4) 删实例行。
            s.delete(row)

            # 5) 账户级联删除：仅专用账户 + 无其他实例引用 + 预留已归零。
            deleted_account_id = None
            if (
                account_row is not None
                and account_row.account_type == "STRATEGY_DEDICATED"
                and Decimal(str(account_row.reserved_cash)) == Decimal("0")
            ):
                still_bound = (
                    s.query(orm.UserStrategyInstanceModel)
                    .filter(
                        orm.UserStrategyInstanceModel.account_id == account_id,
                        orm.UserStrategyInstanceModel.user_strategy_id
                        != user_strategy_id,
                    )
                    .count()
                )
                if still_bound == 0:
                    s.query(orm.NotificationEventModel).filter(
                        orm.NotificationEventModel.account_id == account_id
                    ).update(
                        {orm.NotificationEventModel.account_id: None},
                        synchronize_session=False,
                    )
                    s.delete(account_row)
                    deleted_account_id = account_id

            s.commit()
            return {"deleted": True, "account_id": deleted_account_id}
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
