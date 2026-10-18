"""CANDIDATE 复盘归因 sv 恢复（P3-O）。

缺陷：decision_review_service.review_candidates 把候选按 by_sv.setdefault("",...) 全塞进
     空串一组，插入 decision_review 时 strategy_version_id 恒为 ""——候选池是池级、
     资产表无 sv 列，但每个 asset 通过 pool_id 关联 candidate_pool.strategy_version_id，
     本可 join 恢复。恒空 → 复盘归因全丢、无法按策略统计、BEST/WORST 跨策略乱比。

修复：join candidate_asset.pool_id → candidate_pool.strategy_version_id，按真实 sv 分组，
     插入携带真实 sv，BEST/WORST 在同 sv 组内评。

用 sqlite 真库（StaticPool 跨 session 共享）+ 真实 ORM 播种，直连被测查询逻辑，不打桩。
"""
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)  # 决策日 d0=8/15
RD = date(2026, 8, 22)   # 评价日
D0 = date(2026, 8, 15)   # 基准日（review_date 往前第 5 个交易日）


def _shared_sessionmaker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _bar(_id, symbol, d, close):
    return orm.BarDailyModel(id=_id, instrument_id=f"CN-{symbol}", market="CN",
                             symbol=symbol, trade_date=d, close=close, created_at=_NOW)


def _pool(_id, pool_id, sv):
    return orm.CandidatePoolModel(id=_id, pool_id=pool_id, strategy_version_id=sv,
                                  market="CN", pool_date=D0, pool_type="DAILY",
                                  batch_no=1, candidate_count=3, status="ACTIVE",
                                  created_at=_NOW)


def _asset(_id, cid, pool_id, ts_code):
    return orm.CandidateAssetModel(id=_id, candidate_id=cid, pool_id=pool_id, market="CN",
                                   ts_code=ts_code, candidate_score=1.0, status="ACTIVE",
                                   created_at=_NOW)


def _patch_session(monkeypatch, Session):
    import zhice_os_v2.infrastructure.db.session as sess_mod
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())


def _seed(Session):
    s = Session()
    # 5 个 <RD 的交易日 → offset(4) 落在 8/15 = d0
    for i, d in enumerate([date(2026, 8, 15), date(2026, 8, 18), date(2026, 8, 19),
                           date(2026, 8, 20), date(2026, 8, 21)]):
        s.add(_bar(900 + i, "IDX", d, 100.0))
    # 两个不同 sv 的候选池，各 3 标的
    s.add(_pool(1, "pool-A", "sv-A"))
    s.add(_pool(2, "pool-B", "sv-B"))
    assets = [("c1", "pool-A", "AAA"), ("c2", "pool-A", "BBB"), ("c3", "pool-A", "CCC"),
              ("c4", "pool-B", "DDD"), ("c5", "pool-B", "EEE"), ("c6", "pool-B", "FFF")]
    for i, (cid, pid, ts) in enumerate(assets, start=1):
        s.add(_asset(i, cid, pid, ts))
    # 各标的 d0/rd 收盘 → 收益率区分度（A 组涨幅 AAA>BBB>CCC，B 组 DDD>EEE>FFF）
    closes = {"AAA": (10.0, 13.0), "BBB": (10.0, 11.0), "CCC": (10.0, 9.0),
              "DDD": (20.0, 26.0), "EEE": (20.0, 21.0), "FFF": (20.0, 18.0)}
    bid = 100
    for ts, (c0, c1) in closes.items():
        s.add(_bar(bid, ts, D0, c0))
        bid += 1
        s.add(_bar(bid, ts, RD, c1))
        bid += 1
    s.commit()
    s.close()


def test_candidate_review_recovers_sv_from_pool(monkeypatch):
    """回归：每条 CANDIDATE 复盘的 strategy_version_id 应来自其候选池，而非恒空串。"""
    from zhice_os_v2.application.services import decision_review_service as svc

    Session = _shared_sessionmaker()
    _seed(Session)
    _patch_session(monkeypatch, Session)

    n = svc.review_candidates("CN", RD)
    assert n == 6, f"应写入 6 条复盘，实际 {n}"

    s = Session()
    rows = s.query(orm.DecisionReviewModel).filter_by(decision_type="CANDIDATE").all()
    sv_by_ts = {r.ts_code: r.strategy_version_id for r in rows}
    s.close()

    # 核心断言：sv 不为空，且与候选池一致
    assert sv_by_ts["AAA"] == "sv-A"
    assert sv_by_ts["CCC"] == "sv-A"
    assert sv_by_ts["DDD"] == "sv-B"
    assert sv_by_ts["FFF"] == "sv-B"
    assert all(v for v in sv_by_ts.values()), f"存在空串 sv：{sv_by_ts}"


def test_best_worst_scoped_within_sv_group(monkeypatch):
    """BEST/WORST 应在同 sv 组内评（各 3 标的 → 组内最优 BEST、最差 WORST）。"""
    from zhice_os_v2.application.services import decision_review_service as svc

    Session = _shared_sessionmaker()
    _seed(Session)
    _patch_session(monkeypatch, Session)

    svc.review_candidates("CN", RD)
    s = Session()
    rows = {(r.strategy_version_id, r.ts_code): r.verdict
            for r in s.query(orm.DecisionReviewModel).filter_by(decision_type="CANDIDATE").all()}
    s.close()

    assert rows[("sv-A", "AAA")] == "BEST"
    assert rows[("sv-A", "CCC")] == "WORST"
    assert rows[("sv-B", "DDD")] == "BEST"
    assert rows[("sv-B", "FFF")] == "WORST"
