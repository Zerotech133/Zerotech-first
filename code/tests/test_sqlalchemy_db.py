from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zhice_os_v2.domain.approval.approval import ApprovalRequest
from zhice_os_v2.domain.candidate.candidate import Candidate, CandidatePool
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus
from zhice_os_v2.infrastructure.db import models  # noqa: F401
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return SqlAlchemyDatabase(Session())


def test_sqlalchemy_strategy_roundtrip():
    db = _db()
    strategy = Strategy(
        strategy_id="s1",
        strategy_name="Test",
        market="CN",
        status=StrategyStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.save_strategy(strategy)
    assert db.get_strategy("s1") is not None
    assert len(db.list_strategies()) == 1


def test_sqlalchemy_approval_roundtrip():
    db = _db()
    request = ApprovalRequest(
        request_id="apr-1",
        order_intent_id="oi-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=100,
        price=1700.0,
        reason="test",
    )
    db.save_approval(request)
    loaded = db.get_approval("apr-1")
    assert loaded is not None
    assert loaded.ts_code == "600519.SH"
    assert len(db.list_approvals()) == 1


def test_sqlalchemy_candidate_pool_roundtrip():
    db = _db()
    pool = CandidatePool(
        pool_id="pool-1",
        strategy_version_id="sv-1",
        market="CN",
        trade_date=date(2026, 8, 20),
        candidates=[
            Candidate(
                candidate_id="cand-a",
                strategy_version_id="sv-1",
                market="CN",
                ts_code="600519.SH",
                score=0.85,
                reason="momentum=0.8500",
                factor_values={"momentum": 0.85},
            ),
            Candidate(
                candidate_id="cand-b",
                strategy_version_id="sv-1",
                market="CN",
                ts_code="000001.SZ",
                score=0.42,
                reason="momentum=0.4200",
                factor_values={"momentum": 0.42},
            ),
        ],
    )
    db.save_candidate_pool(pool)

    loaded = db.list_candidates_by_strategy("sv-1")
    assert len(loaded) == 2
    by_code = {c.ts_code: c for c in loaded}
    assert by_code["600519.SH"].score == 0.85
    assert by_code["600519.SH"].strategy_version_id == "sv-1"
    assert by_code["000001.SZ"].reason == "momentum=0.4200"
    # 无关策略号返回空
    assert db.list_candidates_by_strategy("sv-other") == []


def test_sqlalchemy_candidate_pool_upsert_updates_count():
    db = _db()
    pool = CandidatePool(
        pool_id="pool-2",
        strategy_version_id="sv-2",
        market="CN",
        trade_date=date(2026, 8, 20),
        candidates=[
            Candidate(
                candidate_id="cand-x",
                strategy_version_id="sv-2",
                market="CN",
                ts_code="600000.SH",
                score=0.5,
                reason="r",
            )
        ],
    )
    db.save_candidate_pool(pool)
    # 重复保存同 pool_id 不应重复插入候选
    db.save_candidate_pool(pool)
    assert len(db.list_candidates_by_strategy("sv-2")) == 1


def test_latest_snapshot_model_roundtrip():
    """新增 v2_latest_snapshot 表建表 + 唯一键 (market,symbol) 生效。"""
    from sqlalchemy.exc import IntegrityError

    from zhice_os_v2.infrastructure.db import models as orm

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(orm.LatestSnapshotModel(
        market="CN", symbol="000021.SZ",
        snapshot_time=datetime(2026, 8, 20, 6, 55, tzinfo=UTC),
        price=Decimal("37.53"), pre_close=Decimal("37.44"),
        is_current=True, source="v1_sync", created_at=datetime.now(UTC),
    ))
    session.commit()
    row = session.query(orm.LatestSnapshotModel).filter_by(symbol="000021.SZ").one()
    assert float(row.price) == 37.53
    # (market, symbol) 唯一约束：重复插入应报错
    session.add(orm.LatestSnapshotModel(
        market="CN", symbol="000021.SZ",
        snapshot_time=datetime(2026, 8, 20, 7, 0, tzinfo=UTC),
        price=Decimal("37.60"), is_current=True, created_at=datetime.now(UTC),
    ))
    try:
        session.commit()
        raise AssertionError("expected IntegrityError on duplicate (market, symbol)")
    except IntegrityError:
        session.rollback()


def test_sqlalchemy_position_roundtrip():
    db = _db()
    position = PositionLot(
        position_id="pos-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("1700"),
    )
    db.save_position(position)
    positions = db.list_open_positions()
    assert len(positions) == 1
    assert positions[0].position_id == "pos-1"
