"""MembershipRepository / SubscriptionRepository：v2_membership、v2_strategy_subscription 的 CRUD。

同步 session 模式（同 user_repository.py）。ORM 行 → 领域 dataclass 出边界。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from zhice_os_v2.domain.billing.entitlement import Membership, StrategySubscription
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _to_membership(row: orm.MembershipModel) -> Membership:
    return Membership(
        user_id=row.user_id,
        tier=row.tier,
        valid_until=row.valid_until,
        status=row.status,
        is_permanent=getattr(row, "is_permanent", False) or False,
        revoked_at=getattr(row, "revoked_at", None),
        revoked_by_user_id=getattr(row, "revoked_by_user_id", None),
        version=getattr(row, "version", 1) or 1,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_subscription(row: orm.StrategySubscriptionModel) -> StrategySubscription:
    return StrategySubscription(
        user_id=row.user_id,
        strategy_id=row.strategy_id,
        valid_until=row.valid_until,
        status=row.status,
        is_permanent=getattr(row, "is_permanent", False) or False,
        revoked_at=getattr(row, "revoked_at", None),
        revoked_by_user_id=getattr(row, "revoked_by_user_id", None),
        version=getattr(row, "version", 1) or 1,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MembershipRepository:
    def _s(self):
        return get_session()

    def get(self, user_id: str) -> Membership | None:
        s = self._s()
        try:
            row = s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
            return _to_membership(row) if row else None
        finally:
            s.close()

    def get_or_create_free(self, user_id: str) -> Membership:
        """幂等：无记录则建 free 行，有则原样返回。"""
        s = self._s()
        try:
            row = s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
            if row is None:
                now = utc_now()
                row = orm.MembershipModel(
                    user_id=user_id,
                    tier="free",
                    valid_until=None,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
            return _to_membership(row)
        finally:
            s.close()

    def set_member(self, user_id: str, valid_until: datetime) -> Membership:
        """升/续会员：tier=member、写 valid_until、status=active。无行则建。"""
        s = self._s()
        try:
            now = utc_now()
            row = s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
            if row is None:
                row = orm.MembershipModel(
                    user_id=user_id,
                    tier="member",
                    valid_until=valid_until,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
            else:
                row.tier = "member"
                row.valid_until = valid_until
                row.status = "active"
                row.updated_at = now
            s.commit()
            s.refresh(row)
            return _to_membership(row)
        finally:
            s.close()

    @staticmethod
    def _apply_member_extension(
        row: orm.MembershipModel, months: int, now: datetime
    ) -> None:
        """在已锁定的行上叠加续费：base=max(now, 旧到期)，valid_until=base+30*months。"""
        existing = row.valid_until
        if existing is not None and existing.tzinfo is None:
            existing = existing.replace(tzinfo=UTC)
        base = max(now, existing) if existing is not None else now
        row.tier = "member"
        row.valid_until = base + timedelta(days=30 * months)
        row.status = "active"
        row.updated_at = now

    def extend_member(
        self, user_id: str, months: int, now: datetime | None = None
    ) -> Membership:
        """原子续费 months 月：锁行→读旧到期→叠加写回（防并发续费丢更新）。

        无行则建（unique(user_id) 兜底：并发建行触发 IntegrityError→回滚后加锁读回叠加）。
        对齐 account_ledger_service 的 with_for_update 串行化范式。
        """
        now = now or utc_now()
        s = self._s()
        try:
            row = (
                s.query(orm.MembershipModel)
                .filter_by(user_id=user_id)
                .with_for_update()
                .first()
            )
            if row is None:
                row = orm.MembershipModel(
                    user_id=user_id,
                    tier="member",
                    valid_until=now + timedelta(days=30 * months),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:
                    # 并发已建同一 user_id：回滚后加锁读回，按续费叠加。
                    s.rollback()
                    row = (
                        s.query(orm.MembershipModel)
                        .filter_by(user_id=user_id)
                        .with_for_update()
                        .first()
                    )
                    self._apply_member_extension(row, months, now)
                    s.commit()
            else:
                self._apply_member_extension(row, months, now)
                s.commit()
            s.refresh(row)
            return _to_membership(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def mark_expired(self, user_id: str) -> None:
        """到期扫描写回展示标记（tier 保留 member，status→expired）。"""
        s = self._s()
        try:
            row = s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
            if row is not None:
                row.status = "expired"
                row.updated_at = utc_now()
                s.commit()
        finally:
            s.close()

    def list_expired_member_ids(self, now: datetime | None = None) -> list[str]:
        """付费会员且已过期、status 仍 active 的 user_id（供到期扫描停实例）。"""
        now = now or utc_now()
        s = self._s()
        try:
            rows = (
                s.query(orm.MembershipModel.user_id)
                .filter(
                    orm.MembershipModel.tier == "member",
                    orm.MembershipModel.status == "active",
                    # B1：永久/已撤销不在"到期扫描"集合内（永久无到期；撤销另有语义）。
                    orm.MembershipModel.is_permanent.is_(False),
                    orm.MembershipModel.revoked_at.is_(None),
                    orm.MembershipModel.valid_until < now,
                )
                .all()
            )
            return [r[0] for r in rows]
        finally:
            s.close()


class SubscriptionRepository:
    def _s(self):
        return get_session()

    def get(self, user_id: str, strategy_id: str) -> StrategySubscription | None:
        s = self._s()
        try:
            row = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=user_id, strategy_id=strategy_id)
                .first()
            )
            return _to_subscription(row) if row else None
        finally:
            s.close()

    def upsert(
        self, user_id: str, strategy_id: str, valid_until: datetime
    ) -> StrategySubscription:
        s = self._s()
        try:
            now = utc_now()
            row = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=user_id, strategy_id=strategy_id)
                .first()
            )
            if row is None:
                row = orm.StrategySubscriptionModel(
                    user_id=user_id,
                    strategy_id=strategy_id,
                    valid_until=valid_until,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
            else:
                row.valid_until = valid_until
                row.status = "active"
                row.updated_at = now
            s.commit()
            s.refresh(row)
            return _to_subscription(row)
        finally:
            s.close()

    @staticmethod
    def _apply_sub_extension(
        row: orm.StrategySubscriptionModel, months: int, now: datetime
    ) -> None:
        """在已锁定的订阅行上叠加续订：base=max(now, 旧到期)，+30*months。"""
        existing = row.valid_until
        if existing is not None and existing.tzinfo is None:
            existing = existing.replace(tzinfo=UTC)
        base = max(now, existing) if existing is not None else now
        row.valid_until = base + timedelta(days=30 * months)
        row.status = "active"
        row.updated_at = now

    def extend_subscription(
        self, user_id: str, strategy_id: str, months: int, now: datetime | None = None
    ) -> StrategySubscription:
        """原子续订 months 月：锁行→读旧到期→叠加写回（防并发续订丢更新）。

        无行则建（unique(user_id, strategy_id) 兜底：并发建行 IntegrityError→回滚加锁重算）。
        """
        now = now or utc_now()
        s = self._s()
        try:
            row = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=user_id, strategy_id=strategy_id)
                .with_for_update()
                .first()
            )
            if row is None:
                row = orm.StrategySubscriptionModel(
                    user_id=user_id,
                    strategy_id=strategy_id,
                    valid_until=now + timedelta(days=30 * months),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    row = (
                        s.query(orm.StrategySubscriptionModel)
                        .filter_by(user_id=user_id, strategy_id=strategy_id)
                        .with_for_update()
                        .first()
                    )
                    self._apply_sub_extension(row, months, now)
                    s.commit()
            else:
                self._apply_sub_extension(row, months, now)
                s.commit()
            s.refresh(row)
            return _to_subscription(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def list_for_user(self, user_id: str) -> list[StrategySubscription]:
        s = self._s()
        try:
            rows = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=user_id)
                .all()
            )
            return [_to_subscription(r) for r in rows]
        finally:
            s.close()

    def list_active_subscriber_ids(
        self, strategy_id: str, now: datetime | None = None
    ) -> list[str]:
        """某策略当前有效订阅者的 user_id（valid_until>=now）。"""
        now = now or utc_now()
        s = self._s()
        try:
            rows = (
                s.query(orm.StrategySubscriptionModel.user_id)
                .filter(
                    orm.StrategySubscriptionModel.strategy_id == strategy_id,
                    # B1：未撤销 且（永久 或 valid_until>=now）。
                    orm.StrategySubscriptionModel.revoked_at.is_(None),
                    sa.or_(
                        orm.StrategySubscriptionModel.is_permanent.is_(True),
                        orm.StrategySubscriptionModel.valid_until >= now,
                    ),
                )
                .all()
            )
            return [r[0] for r in rows]
        finally:
            s.close()
