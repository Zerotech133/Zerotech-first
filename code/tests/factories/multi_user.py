"""共享合成测试工厂：仅构造内存领域对象，禁止连接生产库。

本任务（Task 1）先建立最小版本：确定性的合成用户。
后续任务会在此扩展 make_public_strategy() / make_private_strategy(owner) /
make_account(owner, type, capital) / make_instance(owner, version, account) /
make_owned_trade_graph(instance) 等构造器。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from zhice_os_v2.domain.accounts.user_account import (
    AccountType,
    UserAccount,
    currency_for_market,
)
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus
from zhice_os_v2.domain.strategy.user_strategy import (
    ExecutionMode,
    UserStrategyInstance,
)


@dataclass(frozen=True)
class SyntheticUsers:
    admin: User
    alice: User
    bob: User


def make_users() -> SyntheticUsers:
    """返回一组确定性的内存用户对象（无 DB 副作用）。"""
    return SyntheticUsers(
        admin=User("u-admin", "admin", Role.ADMIN),
        alice=User("u-alice", "alice", Role.USER),
        bob=User("u-bob", "bob", Role.USER),
    )


def make_public_strategy(name: str = "pub-strategy", market: str = "CN") -> Strategy:
    """平台公共策略（owner=None, PUBLIC/PUBLISHED）。"""
    return Strategy(
        strategy_id=name,
        strategy_name=name,
        market=market,
        status=StrategyStatus.STAGING,
        owner_user_id=None,
        visibility="PUBLIC",
        publication_status="PUBLISHED",
    )


def make_private_strategy(
    owner: User, name: str = "priv-strategy", market: str = "CN"
) -> Strategy:
    """某用户的私有策略（owner=user, PRIVATE/PRIVATE）。"""
    return Strategy(
        strategy_id=name,
        strategy_name=name,
        market=market,
        status=StrategyStatus.DRAFT,
        owner_user_id=owner.user_id,
        visibility="PRIVATE",
        publication_status="PRIVATE",
    )


def make_account(
    owner: User,
    account_type: str | AccountType = AccountType.SHARED_PORTFOLIO,
    initial_capital: Decimal | int | float | str = 100000,
    market: str = "CN",
) -> UserAccount:
    """某用户的持久化账户领域对象（内存，无 DB 副作用；供 Task 5 运行实例引用）。

    currency 由 market 派生、cash_balance 初始化=initial_capital、reserved_cash=0，
    与 UserAccountService.create 口径一致。account_id 防碰撞唯一。
    """
    capital = Decimal(str(initial_capital))
    now = datetime.now(UTC)
    return UserAccount(
        account_id=f"acct-{market}-{secrets.token_hex(6)}",
        user_id=owner.user_id,
        market=market,
        currency=currency_for_market(market),
        account_type=AccountType(account_type),
        initial_capital=capital,
        cash_balance=capital,
        reserved_cash=Decimal("0"),
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )


def make_instance(
    owner: User,
    version,  # StrategyVersion（避免额外 import；只取 .strategy_version_id/.market）
    account: UserAccount,
    *,
    enabled: bool = False,
    auto_execute: bool = False,
    execution_mode: str | ExecutionMode = ExecutionMode.MANUAL,
    capital_quota: Decimal | int | float | str | None = None,
    max_position_ratio: Decimal | int | float | str = "0.1",
) -> UserStrategyInstance:
    """某用户的运行实例领域对象（内存，无 DB 副作用）。

    绑定 owner + 指定版本 + 指定账户；user_strategy_id 防碰撞唯一。account 作为非持久化
    便捷引用一并挂上，便于测试断言 instance.account。
    """
    now = datetime.now(UTC)
    return UserStrategyInstance(
        user_strategy_id=f"ustrat-{secrets.token_hex(6)}",
        user_id=owner.user_id,
        strategy_version_id=version.strategy_version_id,
        account_id=account.account_id,
        enabled=enabled,
        auto_execute=auto_execute,
        execution_mode=ExecutionMode(execution_mode),
        capital_quota=(None if capital_quota is None else Decimal(str(capital_quota))),
        max_position_ratio=Decimal(str(max_position_ratio)),
        created_at=now,
        updated_at=now,
        account=account,
    )
