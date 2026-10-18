"""持久化用户账户领域对象（v2_user_account）。

与旧 `domain/account/account.py`（全局内存持仓视图）区分：这里是**多用户归属**面的
独立资金/风控账本，每个账户固定一个市场与本位币，只属于一个用户。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class AccountType(StrEnum):
    """账户类型。

    - SHARED_PORTFOLIO：可绑定同一用户的多个运行实例（共享组合账户）；
    - STRATEGY_DEDICATED：默认只绑定一个运行实例（独立策略子账户）。
    """

    SHARED_PORTFOLIO = "SHARED_PORTFOLIO"
    STRATEGY_DEDICATED = "STRATEGY_DEDICATED"


# 市场 → 本位币映射（每个账户固定一个市场与本位币）。
_MARKET_CURRENCY: dict[str, str] = {
    "CN": "CNY",
    "US": "USD",
    "HK": "HKD",
    "CRYPTO": "USDT",
}


def currency_for_market(market: str) -> str:
    """由市场派生本位币；未知市场抛 ``account.invalid_market``（fail-fast，不给默认）。"""
    try:
        return _MARKET_CURRENCY[market]
    except KeyError as exc:
        raise ValueError("account.invalid_market") from exc


@dataclass
class UserAccount:
    account_id: str
    user_id: str
    market: str
    currency: str
    account_type: AccountType
    initial_capital: Decimal
    cash_balance: Decimal
    reserved_cash: Decimal = Decimal("0")
    status: str = "ACTIVE"  # ACTIVE / FROZEN / CLOSED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
