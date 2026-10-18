"""持久化用户账户（v2_user_account）测试。

owner 边界：普通用户只能看/取自己的账户；账户创建从 service 派生 currency，
account_id 防碰撞唯一；initial_capital 必须为正；跨用户 get_owned 不泄露。
account_service fixture 用隔离 sqlite + monkeypatch get_session（见 conftest）。
"""
from __future__ import annotations

import pytest

from zhice_os_v2.domain.accounts.user_account import AccountType


def test_user_lists_only_own_accounts(account_service, users):
    own = account_service.create(
        users.alice, market="US", account_type="SHARED_PORTFOLIO", initial_capital=100000
    )
    account_service.create(
        users.bob, market="US", account_type="SHARED_PORTFOLIO", initial_capital=200000
    )
    assert [a.account_id for a in account_service.list_owned(users.alice)] == [own.account_id]


def test_initial_capital_must_be_positive(account_service, users):
    with pytest.raises(ValueError, match="account.invalid_capital"):
        account_service.create(
            users.alice,
            market="CN",
            account_type="STRATEGY_DEDICATED",
            initial_capital=0,
        )


def test_currency_derived_from_market(account_service, users):
    cases = {"CN": "CNY", "US": "USD", "HK": "HKD", "CRYPTO": "USDT"}
    for market, currency in cases.items():
        acct = account_service.create(
            users.alice,
            market=market,
            account_type="SHARED_PORTFOLIO",
            initial_capital=1000,
        )
        assert acct.currency == currency
        assert acct.market == market


def test_unknown_market_is_rejected(account_service, users):
    with pytest.raises(ValueError, match="account.invalid_market"):
        account_service.create(
            users.alice,
            market="MARS",
            account_type="SHARED_PORTFOLIO",
            initial_capital=1000,
        )


def test_account_id_unique_across_users_and_calls(account_service, users):
    ids = set()
    for _ in range(5):
        ids.add(
            account_service.create(
                users.alice,
                market="CN",
                account_type="SHARED_PORTFOLIO",
                initial_capital=1000,
            ).account_id
        )
        ids.add(
            account_service.create(
                users.bob,
                market="CN",
                account_type="SHARED_PORTFOLIO",
                initial_capital=1000,
            ).account_id
        )
    assert len(ids) == 10


def test_create_initializes_balances_and_type(account_service, users):
    from decimal import Decimal

    acct = account_service.create(
        users.alice,
        market="US",
        account_type="STRATEGY_DEDICATED",
        initial_capital=50000,
    )
    assert acct.user_id == users.alice.user_id
    assert acct.account_type == AccountType.STRATEGY_DEDICATED
    assert acct.initial_capital == Decimal("50000")
    assert acct.cash_balance == Decimal("50000")
    assert acct.reserved_cash == Decimal("0")
    assert acct.status == "ACTIVE"


def test_get_owned_returns_none_for_non_owner(account_service, users):
    acct = account_service.create(
        users.alice, market="US", account_type="SHARED_PORTFOLIO", initial_capital=100000
    )
    # owner 能取到
    assert account_service.repository.get_owned(acct.account_id, users.alice.user_id) is not None
    # 非 owner 取不到（返回 None，不泄露存在性）
    assert account_service.repository.get_owned(acct.account_id, users.bob.user_id) is None


def test_list_owned_filters_by_market(account_service, users):
    us = account_service.create(
        users.alice, market="US", account_type="SHARED_PORTFOLIO", initial_capital=1000
    )
    account_service.create(
        users.alice, market="CN", account_type="SHARED_PORTFOLIO", initial_capital=1000
    )
    got = account_service.list_owned(users.alice, market="US")
    assert [a.account_id for a in got] == [us.account_id]
