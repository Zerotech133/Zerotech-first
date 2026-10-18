"""单元 B Task B1-5：统一可授权平台策略谓词。"""
from __future__ import annotations

import pytest

from zhice_os_v2.domain.strategy.eligibility import (
    SUPPORTED_MARKETS,
    deployment_enabled_markets,
    is_grantable_platform_strategy,
)
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus


def _strat(**kw):
    defaults = dict(
        strategy_id="s", strategy_name="S", market="US", owner_user_id=None,
        visibility="PUBLIC", publication_status="PUBLISHED",
        status=StrategyStatus.ACTIVE,
    )
    defaults.update(kw)
    return Strategy(**defaults)


ENABLED = frozenset({"US", "CRYPTO"})


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, True),
        ({"owner_user_id": "u-1"}, False),  # 私有 owner
        ({"visibility": "PRIVATE"}, False),
        ({"publication_status": "PENDING_REVIEW"}, False),
        ({"status": StrategyStatus.DISABLED}, False),
        ({"market": "CN"}, False),  # 未启用市场
    ],
)
def test_grantable_matrix(kw, expected):
    assert is_grantable_platform_strategy(_strat(**kw), enabled_markets=ENABLED) is expected


def test_deployment_enabled_markets_empty_is_all():
    assert deployment_enabled_markets("") == SUPPORTED_MARKETS
    assert deployment_enabled_markets(None) == SUPPORTED_MARKETS


def test_deployment_enabled_markets_filters_and_uppercases():
    assert deployment_enabled_markets("us, crypto") == frozenset({"US", "CRYPTO"})
    # 不受支持的市场被剔除
    assert deployment_enabled_markets("US,XX") == frozenset({"US"})
