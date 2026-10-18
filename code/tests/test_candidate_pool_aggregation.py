"""list_pools 按 strategy_version 各取最新 pool_date 的回归锁。

registry(每日盘前)与 signal(信号驱动)产池节奏不同步：全局 max(pool_date) 会把落后一天的
signal 池挡在最新日外(US 只剩趋势动量)。修复后每 sv 各取自身最新日，两类池并存。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base


@pytest.fixture
def sqlite_pools(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # list_pools / _strategy_names 均在 candidates 模块内调 get_session
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.candidates.get_session", lambda: Session()
    )
    return Session


def _add_pool(Session, pool_id, sv, d, pt):
    with Session() as s:
        s.add(orm.CandidatePoolModel(
            pool_id=pool_id, strategy_version_id=sv, market="US",
            pool_date=d, pool_type=pt, batch_no=1, candidate_count=5,
            status="ACTIVE", created_at=utc_now(),
        ))
        s.commit()


def test_each_sv_takes_own_latest_pool_date(sqlite_pools):
    """TREND(registry) 到 09-04、VIBE(signal) 停在 09-03：两 sv 各取自身最新，都返回；
    TREND 自己的旧 09-03 池不返回（被自身最新 09-04 取代）。"""
    Session = sqlite_pools
    _add_pool(Session, "us_trend_0904", "v1-us-sv-2", date(2026, 9, 4), "PREMARKET")
    _add_pool(Session, "us_trend_0903", "v1-us-sv-2", date(2026, 9, 3), "PREMARKET")  # 旧
    _add_pool(Session, "us_vibe_0903", "v1-us-sv-3", date(2026, 9, 3), "SIGNAL")

    from zhice_os_v2.api.routers.candidates import list_pools
    r = list_pools("US")

    assert {p["pool_id"] for p in r} == {"us_trend_0904", "us_vibe_0903"}
    assert {p["strategy_version_id"] for p in r} == {"v1-us-sv-2", "v1-us-sv-3"}
    # 落后一天的 signal 池没有被全局 max 挡掉
    assert any(p["pool_type"] == "SIGNAL" for p in r)


def test_explicit_date_filter_unaffected(sqlite_pools):
    """指定 date 时仍按该日精确过滤（不走 per-sv 最新逻辑）。"""
    Session = sqlite_pools
    _add_pool(Session, "us_trend_0904", "v1-us-sv-2", date(2026, 9, 4), "PREMARKET")
    _add_pool(Session, "us_vibe_0903", "v1-us-sv-3", date(2026, 9, 3), "SIGNAL")

    from zhice_os_v2.api.routers.candidates import list_pools
    r = list_pools("US", date="2026-09-03")
    assert {p["pool_id"] for p in r} == {"us_vibe_0903"}
