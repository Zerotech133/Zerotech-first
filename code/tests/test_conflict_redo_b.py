"""重做B（冲突裁决双保险）真库回归测试（对齐 spec 2026-08-26-structural-id-fix-redo-b §3.3）。

第一重：resolve_plan_conflicts 裁决败者时回退其授权。plan_id 唯一带 sv →
        order_intent_id==loser.plan_id 只命中败者、不误伤同标的赢家（回归旧B
        "共享 base 误伤赢家"缺陷）。
第二重：_query_entry_approvals outerjoin 排除对应计划已作废（CANCELLED/EXPIRED/
        REJECTED）的授权（即便第一重漏回退），无对应计划的历史授权仍放行。

用 sqlite in-memory（StaticPool 跨 session 共享同一库）+ 真实 ORM 播种，直连被测
查询逻辑，不打桩。
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base


def _shared_sessionmaker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# sqlite 对 BigInteger 主键不自增（仅 INTEGER PK 自增）→ 显式给 id 避免 NOT NULL。
def _sv(_id, sv_id, status):
    return orm.StrategyVersionModel(
        id=_id, strategy_version_id=sv_id, strategy_id="s", version_no="v1",
        market="CN", status=status, spec={}, created_at=datetime.now(UTC))


def _plan(_id, plan_id, sv_id, status, conf):
    return orm.TradePlanModel(
        id=_id, plan_id=plan_id, candidate_id="cand-" + plan_id, strategy_version_id=sv_id,
        market="CN", ts_code="000001.SZ", plan_status=status,
        confidence_level=conf, created_at=datetime.now(UTC))


def _apr(_id, req_id, oi_id, sv_id, status):
    return orm.ApprovalRequestModel(
        id=_id, request_id=req_id, order_intent_id=oi_id, strategy_version_id=sv_id,
        market="CN", ts_code="000001.SZ", direction="BUY", qty=100,
        status=status, channel="AUTO", token="t", created_at=datetime.now(UTC))


def test_conflict_reverts_loser_approval_not_winner(monkeypatch):
    """同标的两策略（PROD 赢 / DRAFT 败）：败者授权回退为 CANCELLED，赢家授权不受影响。"""
    Session = _shared_sessionmaker()
    seed = Session()
    seed.add_all([
        _sv(1, "sv-win", "PROD"), _sv(2, "sv-lose", "DRAFT"),
        _plan(1, "P_WIN", "sv-win", "APPROVED", 0.9),
        _plan(2, "P_LOSE", "sv-lose", "APPROVED", 0.5),
        _apr(1, "apr-win", "P_WIN", "sv-win", "APPROVED"),
        _apr(2, "apr-lose", "P_LOSE", "sv-lose", "APPROVED"),
    ])
    seed.commit()
    seed.close()

    import zhice_os_v2.infrastructure.db.session as sess_mod
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())

    from zhice_os_v2.application.services.conflict_resolver_service import (
        resolve_plan_conflicts,
    )
    result = resolve_plan_conflicts("CN")

    assert result["cancelled_plan_ids"] == ["P_LOSE"]
    assert result["reverted_approvals"] == 1
    chk = Session()
    # 赢家授权/计划不被误伤（回归旧B共享 base 误伤缺陷）
    assert chk.query(orm.ApprovalRequestModel).filter_by(
        request_id="apr-win").one().status == "APPROVED"
    assert chk.query(orm.TradePlanModel).filter_by(
        plan_id="P_WIN").one().plan_status == "APPROVED"
    # 败者授权/计划均 CANCELLED
    assert chk.query(orm.ApprovalRequestModel).filter_by(
        request_id="apr-lose").one().status == "CANCELLED"
    assert chk.query(orm.TradePlanModel).filter_by(
        plan_id="P_LOSE").one().plan_status == "CANCELLED"
    chk.close()


def test_entry_query_excludes_dead_plan_but_keeps_orphan():
    """入场授权查询：对应计划 CANCELLED 的授权被排除；无对应计划的历史授权仍放行。"""
    Session = _shared_sessionmaker()
    seed = Session()
    seed.add_all([
        _plan(1, "P_ACTIVE", "sv-a", "APPROVED", 0.5),
        _plan(2, "P_DEAD", "sv-b", "CANCELLED", 0.5),
        _apr(1, "apr-active", "P_ACTIVE", "sv-a", "APPROVED"),
        _apr(2, "apr-dead", "P_DEAD", "sv-b", "APPROVED"),
        _apr(3, "apr-orphan", "P_NONE", "sv-c", "APPROVED"),  # 无对应计划
    ])
    seed.commit()
    seed.close()

    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    svc = WorkflowService(InMemoryDatabase())
    profile = SimpleNamespace(tz=UTC)
    rows = svc._query_entry_approvals(Session(), "CN", datetime.now(UTC).date(), profile)

    oi = {r.order_intent_id for r in rows}
    assert oi == {"P_ACTIVE", "P_NONE"}, f"败者(CANCELLED计划)应排除、孤儿授权应放行，实得 {oi}"
