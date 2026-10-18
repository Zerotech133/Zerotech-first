"""catch-up 幂等改判（2026-09-02 遗留B）。

旧判据「当日有候选池」误判：pre_market 可建池后因 sim_max_positions 上限早退/异常留下
plans=0，仍被认作已跑而不补。新判据 _premarket_needs_catchup：完成 = 当日有计划(plans>0)
或 当日有池但候选合计为 0（正当空振日）；否则（无池 / 有候选却无计划）判定未完成需补跑。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.scheduler import _premarket_needs_catchup

TODAY = date(2026, 9, 2)
TZ = get_profile("CN").tz
_AT = datetime(2026, 9, 2, 9, 25, tzinfo=TZ)


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_pool(s, count: int, market: str = "CN", pid: str = "pool1"):
    s.add(orm.CandidatePoolModel(
        pool_id=pid, strategy_version_id="v1-cn-sv-1", market=market, pool_date=TODAY,
        pool_type="PREMARKET", candidate_count=count, status="ACTIVE", created_at=_AT))
    s.commit()


def _add_plan(s, market: str = "CN"):
    # sqlite 下 BigInteger 主键不自增，显式给 id
    s.add(orm.TradePlanModel(
        id=1, plan_id="pl1", candidate_id="c1", strategy_version_id="v1-cn-sv-1",
        market=market, ts_code="600000.SH", plan_status="APPROVED", created_at=_AT))
    s.commit()


def test_no_pool_needs_catchup():
    """当日无任何候选池 → 未跑，需补。"""
    s = _session()
    assert _premarket_needs_catchup(s, "CN", TODAY, TZ) is True


def test_pool_with_candidates_but_no_plan_needs_catchup():
    """有候选池(20 候选)但无计划（建池后早退/异常）→ 未完成，需补。"""
    s = _session()
    _add_pool(s, 20)
    assert _premarket_needs_catchup(s, "CN", TODAY, TZ) is True


def test_plan_exists_no_catchup():
    """当日已产计划 → 完成，不补。"""
    s = _session()
    _add_pool(s, 20)
    _add_plan(s)
    assert _premarket_needs_catchup(s, "CN", TODAY, TZ) is False


def test_empty_pool_zero_candidates_no_catchup():
    """有池但候选合计为 0（正当空振日）→ 完成，不补（避免空日每15分钟重复选股）。"""
    s = _session()
    _add_pool(s, 0)
    assert _premarket_needs_catchup(s, "CN", TODAY, TZ) is False


def test_other_market_isolated():
    """只看目标 market：US 有计划不影响 CN 判定。"""
    s = _session()
    _add_plan(s, market="US")
    assert _premarket_needs_catchup(s, "CN", TODAY, TZ) is True
