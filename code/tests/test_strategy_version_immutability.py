"""策略版本不可变 + create_version（下一版本）测试（内存后端，无真库）。

不可变规则（update_spec 守卫）：版本被 UserStrategyInstance/TradePlan/Position/Replay
任一引用、或版本状态 ACTIVE、或内置类型 → 抛 ValueError（引用/ACTIVE 统一
"strategy.version_immutable"；内置保留原有更具体的中文消息）。未被引用的非活跃外部版本
仍可改。

create_version（owner-scoped）：私有策略仅 owner（非 owner not-found 等价 LookupError）、
公共策略仅 ADMIN（非 ADMIN PermissionError）；version_no = 现有最大数值 +1（"vN"），新
strategy_version_id ≠ 旧、不改动任何旧版本行。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from zhice_os_v2.application.services.strategy_catalog_service import (
    StrategyCatalogService,
)
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import (
    Strategy,
    StrategyStatus,
    StrategyVersion,
)
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _spec(market: str = "US", **kw) -> StrategySpec:
    base = {"spec_version": 1, "market": market, "universe": {"type": "all"}}
    base.update(kw)
    return StrategySpec.model_validate(base)


@pytest.fixture
def db() -> InMemoryDatabase:
    return InMemoryDatabase()


@pytest.fixture
def strategy_service(db) -> StrategyService:
    return StrategyService(db)


def _make_private(db, owner: User, market: str = "US"):
    """建一个 owner 的私有策略 + v1（STAGING、未引用）。返回 (strategy, v1)。"""
    catalog = StrategyCatalogService(db)
    strat = catalog.create_private(owner, "alpha", market=market)
    v1 = next(
        v for v in db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )
    return strat, v1


# ---------- 不可变 ----------
def test_referenced_version_cannot_be_updated(strategy_service, db, users):
    _strat, v1 = _make_private(db, users.alice)
    # 引用该版本：落一笔持仓（Position 引用 strategy_version_id）。
    db.save_position(
        PositionLot(
            position_id="pos-1",
            strategy_version_id=v1.strategy_version_id,
            market="US",
            ts_code="AAPL",
            direction="BUY",
            open_qty=Decimal("1"),
            open_price=Decimal("1"),
        )
    )
    with pytest.raises(ValueError, match="strategy.version_immutable"):
        strategy_service.update_spec(v1.strategy_version_id, _spec())


def test_active_version_cannot_be_updated(strategy_service, db):
    db.save_strategy(
        Strategy(
            strategy_id="s-active",
            strategy_name="x",
            market="US",
            strategy_type="V2_EXTERNAL",
            status=StrategyStatus.ACTIVE,
        )
    )
    db.save_strategy_version(
        StrategyVersion(
            strategy_version_id="s-active-v1",
            strategy_id="s-active",
            version_no="v1",
            market="US",
            spec=_spec(),
            status=StrategyStatus.ACTIVE,
        )
    )
    with pytest.raises(ValueError, match="strategy.version_immutable"):
        strategy_service.update_spec("s-active-v1", _spec())


def test_unreferenced_inactive_version_can_be_updated(strategy_service, db, users):
    _strat, v1 = _make_private(db, users.alice)  # STAGING、未引用
    out = strategy_service.update_spec(
        v1.strategy_version_id,
        _spec(factors=[{"name": "rsi", "params": {"window": 14}}]),
    )
    got = db.get_strategy_version(out.strategy_version_id)
    assert got.spec.factors[0].name == "rsi"


# ---------- create_version ----------
def test_create_version_creates_next_version(strategy_service, db, users):
    strat, v1 = _make_private(db, users.alice)
    v2 = strategy_service.create_version(users.alice, strat.strategy_id, _spec())
    assert v2.version_no == "v2"
    assert v2.strategy_version_id != v1.strategy_version_id
    assert v2.strategy_id == strat.strategy_id
    assert v2.status == StrategyStatus.STAGING
    # 旧版本行未被改动。
    old = db.get_strategy_version(v1.strategy_version_id)
    assert old is not None
    assert old.version_no == "v1"
    assert old.strategy_version_id == v1.strategy_version_id


def test_create_version_private_non_owner_not_found(strategy_service, db, users):
    strat, _v1 = _make_private(db, users.alice)
    with pytest.raises(LookupError):
        strategy_service.create_version(users.bob, strat.strategy_id, _spec())


def test_create_version_public_requires_admin(strategy_service, db, users):
    catalog = StrategyCatalogService(db)
    pub = catalog.create_public("pub-x", market="US")
    with pytest.raises(PermissionError):
        strategy_service.create_version(users.alice, pub.strategy_id, _spec())
    v2 = strategy_service.create_version(users.admin, pub.strategy_id, _spec())
    assert v2.version_no == "v2"
    assert v2.strategy_id == pub.strategy_id


def test_create_version_unknown_strategy_not_found(strategy_service, users):
    with pytest.raises(LookupError):
        strategy_service.create_version(users.admin, "no-such-strategy", _spec())
