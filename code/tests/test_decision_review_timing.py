"""复盘 job 时序护栏：评价日今日日K未入库时不产脏 0（就绪门）。

缺陷（与 T+1「今日日K滞后」同源）：run_decision_review_job 原 15:50 触发，早于
    fetch_daily 16:05 当日日K落库（US/HK 15:50 甚至尚未收盘）→ review_candidates/
    missed 依赖评价日 rd 的收盘价 `_closes(market,ts,d0,rd)`，rd 那天日K缺失即返回
    None → rets 空 → candidates 恒 0（8-27 CN 中招，复盘空产）。

修复两层：
    ① scheduler 把复盘挪到 fetch_daily 之后（16:30，见 test_scheduler_jobs_registration）；
    ② 就绪门：run_decision_reviews 检查评价日日K是否已入库，未就绪 fail-loud 返回
       skipped、不产脏 0（防 fetch_daily 偶发延迟时静默算空覆盖真实归因窗口）。

用 sqlite 真库（StaticPool 跨 session 共享）+ 真实 ORM 播种，直连被测查询，不打桩。
"""
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
RD = date(2026, 8, 22)   # 评价日
D0 = date(2026, 8, 15)   # 基准日（往前第 5 个交易日）


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


def _seed_base(Session, *, seed_rd_daily: bool):
    """播种：5 个 <RD 交易日 IDX 基准 + 候选池 + 各标的 d0 收盘。

    seed_rd_daily=True 时额外播种各标的 RD 当日日K（评价日就绪）；False 模拟今日日K未入库。
    """
    s = Session()
    for i, d in enumerate([date(2026, 8, 15), date(2026, 8, 18), date(2026, 8, 19),
                           date(2026, 8, 20), date(2026, 8, 21)]):
        s.add(_bar(900 + i, "IDX", d, 100.0))
    s.add(_pool(1, "pool-A", "sv-A"))
    assets = [("c1", "pool-A", "AAA"), ("c2", "pool-A", "BBB"), ("c3", "pool-A", "CCC")]
    for i, (cid, pid, ts) in enumerate(assets, start=1):
        s.add(_asset(i, cid, pid, ts))
    closes = {"AAA": (10.0, 13.0), "BBB": (10.0, 11.0), "CCC": (10.0, 9.0)}
    bid = 100
    for ts, (c0, c1) in closes.items():
        s.add(_bar(bid, ts, D0, c0))
        bid += 1
        if seed_rd_daily:
            s.add(_bar(bid, ts, RD, c1))
            bid += 1
    s.commit()
    s.close()


def test_reviews_skipped_when_today_daily_not_ready(monkeypatch):
    """评价日日K未入库 → run_decision_reviews 返回 skipped，四类全 0 且不产脏数据。"""
    from zhice_os_v2.application.services import decision_review_service as svc

    Session = _shared_sessionmaker()
    _seed_base(Session, seed_rd_daily=False)  # 不播种 RD 当日日K
    _patch_session(monkeypatch, Session)

    result = svc.run_decision_reviews("CN", RD)

    assert result.get("skipped") == "daily_not_ready", f"应被就绪门拦截：{result}"
    assert result["candidates"] == 0
    assert result["entries"] == 0
    assert result["exits"] == 0
    assert result["missed"] == 0

    s = Session()
    n = s.query(orm.DecisionReviewModel).count()
    s.close()
    assert n == 0, f"就绪门未通过不应写入任何复盘，实际 {n}"


def test_reviews_run_when_today_daily_ready(monkeypatch):
    """评价日日K已入库 → 就绪门放行，CANDIDATE 正常产出（回归：门不误伤正常路径）。"""
    from zhice_os_v2.application.services import decision_review_service as svc

    Session = _shared_sessionmaker()
    _seed_base(Session, seed_rd_daily=True)
    _patch_session(monkeypatch, Session)

    result = svc.run_decision_reviews("CN", RD)

    assert "skipped" not in result, f"日K就绪不应跳过：{result}"
    assert result["candidates"] == 3, f"应产 3 条 CANDIDATE 复盘，实际 {result['candidates']}"
