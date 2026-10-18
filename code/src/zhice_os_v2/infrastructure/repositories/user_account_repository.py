"""UserAccountRepository: v2_user_account 表的 owner-scoped 持久化。

与 user_repository.py 同范式：独立类，每方法自开 session、try/finally close，需要行锁
用 with_for_update()。所有查询强制带 user_id owner 边界（get_owned 非 owner 返回 None，
不泄露存在性）。
"""
from __future__ import annotations

from zhice_os_v2.domain.accounts.user_account import AccountType, UserAccount
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _to_domain(row: orm.UserAccountModel) -> UserAccount:
    return UserAccount(
        account_id=row.account_id,
        user_id=row.user_id,
        market=row.market,
        currency=row.currency,
        account_type=AccountType(row.account_type),
        initial_capital=row.initial_capital,
        cash_balance=row.cash_balance,
        reserved_cash=row.reserved_cash,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class UserAccountRepository:
    def _s(self):
        return get_session()

    def create(self, account: UserAccount) -> UserAccount:
        s = self._s()
        try:
            row = orm.UserAccountModel(
                account_id=account.account_id,
                user_id=account.user_id,
                market=account.market,
                currency=account.currency,
                account_type=account.account_type.value,
                initial_capital=account.initial_capital,
                cash_balance=account.cash_balance,
                reserved_cash=account.reserved_cash,
                status=account.status,
                created_at=account.created_at,
                updated_at=account.updated_at,
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

    def list_by_user(self, user_id: str, market: str | None = None) -> list[UserAccount]:
        s = self._s()
        try:
            q = s.query(orm.UserAccountModel).filter_by(user_id=user_id)
            if market is not None:
                q = q.filter_by(market=market)
            rows = q.order_by(orm.UserAccountModel.created_at, orm.UserAccountModel.id).all()
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def list_all(self) -> list[UserAccount]:
        """全部用户账户（无 owner 边界；仅供 ADMIN 全局查看 API 使用）。"""
        s = self._s()
        try:
            rows = (
                s.query(orm.UserAccountModel)
                .order_by(orm.UserAccountModel.created_at, orm.UserAccountModel.id)
                .all()
            )
            return [_to_domain(r) for r in rows]
        finally:
            s.close()

    def get_owned(
        self, account_id: str, user_id: str, *, for_update: bool = False
    ) -> UserAccount | None:
        s = self._s()
        try:
            q = s.query(orm.UserAccountModel).filter_by(
                account_id=account_id, user_id=user_id
            )
            if for_update:
                q = q.with_for_update()
            row = q.first()
            return _to_domain(row) if row is not None else None
        finally:
            s.close()
