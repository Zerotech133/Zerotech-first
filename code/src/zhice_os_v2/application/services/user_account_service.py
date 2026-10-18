"""用户账户服务：创建账户（owner 取自入参 user）与 owner-scoped 列表。

契约（按计划 Task 4）：
- create(user, market, account_type, initial_capital) -> UserAccount
  owner=user.user_id；initial_capital<=0 抛 ValueError("account.invalid_capital")；
  cash_balance 初始化=initial_capital，reserved_cash=0；currency 由 market 派生。
- list_owned(user, market=None) -> list[UserAccount]
account_id 用防碰撞唯一值 f"acct-{market}-{token_hex(6)}"。
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from zhice_os_v2.domain.accounts.user_account import (
    AccountType,
    UserAccount,
    currency_for_market,
)
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.repositories.user_account_repository import (
    UserAccountRepository,
)


class UserAccountService:
    def __init__(self, repository: UserAccountRepository | None = None) -> None:
        self.repository = repository or UserAccountRepository()

    def create(
        self,
        user: User,
        market: str,
        account_type: str | AccountType,
        initial_capital: Decimal | int | float | str,
    ) -> UserAccount:
        try:
            capital = Decimal(str(initial_capital))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("account.invalid_capital") from exc
        if capital <= 0:
            raise ValueError("account.invalid_capital")

        currency = currency_for_market(market)  # 未知市场抛 account.invalid_market
        acc_type = AccountType(account_type)

        now = datetime.now(UTC)
        account = UserAccount(
            account_id=f"acct-{market}-{secrets.token_hex(6)}",
            user_id=user.user_id,
            market=market,
            currency=currency,
            account_type=acc_type,
            initial_capital=capital,
            cash_balance=capital,
            reserved_cash=Decimal("0"),
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        return self.repository.create(account)

    def list_owned(self, user: User, market: str | None = None) -> list[UserAccount]:
        return self.repository.list_by_user(user.user_id, market=market)

    def list_all(self) -> list[UserAccount]:
        """全部账户（无 owner 边界；仅供 ADMIN 全局查看 API 使用）。"""
        return self.repository.list_all()
