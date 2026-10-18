"""CandidateService 落库归属与 persist 守卫测试。"""
from datetime import date

from zhice_os_v2.application.services.candidate_service import CandidateService
from zhice_os_v2.domain.strategy.spec import FactorSpec, StrategySpec
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _spec() -> StrategySpec:
    return StrategySpec(
        spec_version=1,
        market="CN",
        universe={"codes": ["600519.SH", "000001.SZ"]},
        factors=[FactorSpec(name="momentum", params={"window": 3})],
    )


def _bars(ts_code: str, trade_date: date, window: int) -> list[float]:
    # 单调上升，保证 momentum 有值
    return [10.0 + i for i in range(window + 1)]


def test_generate_threads_strategy_version_id():
    db = InMemoryDatabase()
    svc = CandidateService(db)
    pool = svc.generate(_spec(), date(2026, 8, 20), _bars, strategy_version_id="sv-1")

    assert pool.strategy_version_id == "sv-1"
    assert all(c.strategy_version_id == "sv-1" for c in pool.candidates)
    # candidate_id 含策略号前缀，防多策略撞 id
    assert all(c.candidate_id.startswith("cand-sv-1-") for c in pool.candidates)
    # 已落库，可按策略号查回
    assert len(db.list_candidates_by_strategy("sv-1")) == len(pool.candidates)


def test_generate_persist_false_skips_db():
    db = InMemoryDatabase()
    svc = CandidateService(db)
    pool = svc.generate(_spec(), date(2026, 8, 20), _bars, strategy_version_id="sv-1", persist=False)

    assert pool.candidates  # 计算照常发生
    assert db.list_candidates_by_strategy("sv-1") == []  # 但未落库


def test_two_strategies_no_candidate_id_collision():
    db = InMemoryDatabase()
    svc = CandidateService(db)
    svc.generate(_spec(), date(2026, 8, 20), _bars, strategy_version_id="sv-A")
    svc.generate(_spec(), date(2026, 8, 20), _bars, strategy_version_id="sv-B")

    a = db.list_candidates_by_strategy("sv-A")
    b = db.list_candidates_by_strategy("sv-B")
    assert len(a) == 2 and len(b) == 2
    assert {c.candidate_id for c in a}.isdisjoint(c.candidate_id for c in b)
