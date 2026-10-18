"""WorkflowService._persist_signal_pool 落库 + 幂等单测。

信号型(spec)策略走 run_spec_signals，原本从不写候选池；本功能让其 enter 分支
的候选按「每 sv 每交易日一池、pool_type=SIGNAL」落库，与 registry 池并列。

_persist_signal_pool 内部走 get_session() 直连真 PG（绕过注入的 InMemoryDatabase），
故这里 monkeypatch get_session 指向共享内存 sqlite（StaticPool 保证多 session 同库），
只验证落库/幂等逻辑本身，不依赖真库。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


@pytest.fixture
def sqlite_sessions(monkeypatch):
    """共享内存 sqlite：StaticPool 让每次 get_session() 都命中同一个库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: Session(),
    )
    return Session


def _dump(Session):
    with Session() as s:
        pools = s.execute(select(orm.CandidatePoolModel)).scalars().all()
        assets = s.execute(select(orm.CandidateAssetModel)).scalars().all()
        # detach 前取值
        return (
            [(p.pool_id, p.pool_type, p.strategy_version_id, p.candidate_count, p.status)
             for p in pools],
            {a.ts_code: (a.pool_id, float(a.candidate_score), a.candidate_reason, a.status)
             for a in assets},
        )


def test_creates_signal_pool_and_assets(sqlite_sessions):
    Session = sqlite_sessions
    svc = WorkflowService(InMemoryDatabase())
    td = date(2026, 9, 2)
    cands = [
        {"ts_code": "AAPL", "score": 1.5, "reason": "信号入场(1d)"},
        {"ts_code": "MSFT", "score": 2.0, "reason": "信号入场(1d)"},
    ]
    pid = svc._persist_signal_pool("US", td, "v1-us-sv-3", cands)

    assert pid == "us_pool_20260902_spec_v1-us-sv-3"
    pools, assets = _dump(Session)
    assert len(pools) == 1
    assert pools[0] == (pid, "SIGNAL", "v1-us-sv-3", 2, "ACTIVE")
    assert set(assets) == {"AAPL", "MSFT"}
    assert assets["AAPL"][0] == pid  # asset 指向该池


def test_idempotent_upsert_same_sv_same_day(sqlite_sessions):
    Session = sqlite_sessions
    svc = WorkflowService(InMemoryDatabase())
    td = date(2026, 9, 2)
    sv = "v1-hk-sv-2"

    svc._persist_signal_pool("HK", td, sv, [{"ts_code": "0700.HK", "score": 1.0, "reason": "r1"}])
    # 二次调用同 sv 同日：0700 分数/理由变、并新增 9988 → 应 upsert 而非重复插入
    pid = svc._persist_signal_pool("HK", td, sv, [
        {"ts_code": "0700.HK", "score": 9.9, "reason": "r2"},
        {"ts_code": "9988.HK", "score": 3.0, "reason": "r1"},
    ])

    pools, assets = _dump(Session)
    assert len(pools) == 1  # 幂等：仍单池，无第二池
    assert pools[0][0] == pid
    assert pools[0][3] == 2  # candidate_count 已刷新为 2
    assert set(assets) == {"0700.HK", "9988.HK"}
    assert assets["0700.HK"][1] == 9.9  # score 被更新
    assert assets["0700.HK"][2] == "r2"  # reason 被更新


def test_noop_on_empty_candidates_or_missing_sv(sqlite_sessions):
    Session = sqlite_sessions
    svc = WorkflowService(InMemoryDatabase())
    td = date(2026, 9, 2)

    assert svc._persist_signal_pool("US", td, "sv", []) is None
    assert svc._persist_signal_pool("US", td, "", [{"ts_code": "X", "score": 1.0}]) is None

    pools, assets = _dump(Session)
    assert pools == []
    assert assets == {}
