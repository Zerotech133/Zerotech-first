"""Bug A 回归：出场价污染。

真跑 `_load_plan_exits` 本体（sqlite 播种真实 TradePlan 行，patch get_session）与
`run_intraday` 应用处取值，验证：
  ① 只取活跃/可执行态计划（PENDING/APPROVED/EXECUTED），排除 CANCELLED/EXPIRED/REJECTED；
  ② 按 (ts_code, strategy_version_id) 取值，杜绝跨策略串价；
  ③ 取不到本策略计划时走持仓自身 ExitPolicy 兜底，不用污染值。
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zhice_os_v2.application.services import workflow_service as wf
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

MARKET = "CRYPTO"
CODE = "BTCUSDT"
SV1 = "v1-crypto-sv-1"
SV3 = "v1-crypto-sv-3"


@pytest.fixture
def sql_session(monkeypatch):
    """sqlite 内存库 + patch get_session（让 _load_plan_exits 真跑到真实 ORM 查询）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("zhice_os_v2.infrastructure.db.session.get_session",
                        lambda: Session())
    return Session


_ID = [0]


def _plan(session, plan_id, sv, status, tp, stop, created_at):
    _ID[0] += 1
    session.add(orm.TradePlanModel(
        id=_ID[0],
        plan_id=plan_id, candidate_id=f"c-{plan_id}", strategy_version_id=sv,
        market=MARKET, ts_code=CODE, plan_status=status,
        ref_price=Decimal("100000"),
        stop_loss_price=Decimal(str(stop)) if stop is not None else None,
        take_profit_price=Decimal(str(tp)) if tp is not None else None,
        created_at=created_at,
    ))


def test_load_plan_exits_filters_status_and_keys_by_sv(sql_session):
    """CANCELLED 计划最新但被排除；sv-3 的低止盈不落到 sv-1 键上。"""
    s = sql_session()
    base = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    # sv-1 的活跃 EXECUTED 计划（正常高止盈）
    _plan(s, "p-sv1", SV1, "EXECUTED", tp=200000, stop=95000, created_at=base)
    # sv-3 的活跃 EXECUTED 计划（低止盈——BTC 被套的污染源）
    _plan(s, "p-sv3", SV3, "EXECUTED", tp=86598, stop=80000, created_at=base + timedelta(hours=1))
    # sv-1 的 CANCELLED 计划（最新，但作废——旧口径会误取它）
    _plan(s, "p-sv1-x", SV1, "CANCELLED", tp=1, stop=1, created_at=base + timedelta(hours=2))
    s.commit()
    s.close()

    out = wf._load_plan_exits([CODE], MARKET)
    # 键为 (ts_code, sv)
    assert (CODE, SV1) in out
    # sv-1 取到自身活跃计划（200000），绝不取 CANCELLED 的 1 也不取 sv-3 的 86598
    assert out[(CODE, SV1)] == (Decimal("95000"), Decimal("200000"))
    # sv-3 取自身计划，互不串价
    assert out[(CODE, SV3)] == (Decimal("80000"), Decimal("86598"))


def test_run_intraday_not_polluted_by_foreign_plan(sql_session, monkeypatch):
    """sv-1 持仓不应被 sv-3 计划 / CANCELLED 计划的低止盈误平——走自身策略 policy 兜底。"""
    s = sql_session()
    base = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    # 仅存 sv-3 活跃低止盈 + sv-1 CANCELLED 低止盈（sv-1 无任何活跃计划）
    _plan(s, "p-sv3", SV3, "EXECUTED", tp=86598, stop=80000, created_at=base + timedelta(hours=1))
    _plan(s, "p-sv1-x", SV1, "CANCELLED", tp=90000, stop=88000, created_at=base + timedelta(hours=2))
    s.commit()
    s.close()

    db = InMemoryDatabase()
    lot = PositionLot(
        position_id="pos-btc", strategy_version_id=SV1, market=MARKET,
        ts_code=CODE, direction="BUY", open_qty=Decimal("1"),
        open_price=Decimal("100000"), opened_at=utc_now() - timedelta(days=3),
    )
    db.save_position(lot)
    # 现价 105000：> 污染止盈(86598/90000) 但 < 默认策略止盈(100000×1.10=110000)。
    # 旧代码（按 ts_code 取到 CANCELLED 的 90000）→ 误止盈平仓；
    # 修复后（sv-1 无活跃计划 → policy 兜底 110000）→ 不平。
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda symbols, market: {CODE: 105000.0})
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_pre_closes",
                        lambda symbols, market: {CODE: 100000.0})

    r = WorkflowService(db).run_intraday(MARKET)
    assert r["take_profit_triggered"] == []
    assert r["stop_loss_triggered"] == []
    assert db.positions["pos-btc"].status == PositionStatus.OPEN
