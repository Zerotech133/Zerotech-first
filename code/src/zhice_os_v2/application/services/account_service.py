"""多账户服务。"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.domain.account.account import AccountSnapshot


class AccountService:
    def __init__(self) -> None:
        self._accounts: dict[str, AccountSnapshot] = {}

    def create_account(
        self,
        account_id: str,
        market: str,
        initial_capital: Decimal,
    ) -> AccountSnapshot:
        account = AccountSnapshot(
            account_id=account_id,
            market=market,
            initial_capital=initial_capital,
            cash=initial_capital,
        )
        self._accounts[account_id] = account
        return account

    def get_account(self, account_id: str) -> AccountSnapshot | None:
        return self._accounts.get(account_id)

    def list_accounts(self) -> list[AccountSnapshot]:
        return list(self._accounts.values())
