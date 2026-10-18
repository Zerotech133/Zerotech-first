"""claim/release 对称性 + _auto_execute 未成交回滚（2026-09-02 EXECUTING 固着修复）。

根因：_auto_execute claim(APPROVED→EXECUTING) 后若 apply_fill 返回 None(未成交)，
旧代码 return None 不回滚 → 承认固着 EXECUTING，永久占用 sim_max_positions 上限。
修复：失败/异常时 release_approval_claim(EXECUTING→APPROVED)，当日可重试且不占坑。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services import execution_service as exec_mod
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase

_PLAN_ID = "plan-cand-sv-x-000001.SZ-2026-09-01"
_RID = "apr-" + _PLAN_ID


def _shared_sessionmaker():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _approval() -> ApprovalRequest:
    a = ApprovalRequest(
        request_id=_RID, order_intent_id=_PLAN_ID, strategy_version_id="sv-x",
        market="CN", ts_code="000001.SZ", direction="BUY", qty=100, price=10.0,
        reason="test",
    )
    a.approve(actor="test")
    return a


def test_release_reverses_claim():
    """claim(APPROVED→EXECUTING) 与 release(EXECUTING→APPROVED) 对称；重复 release 幂等 no-op。"""
    db = SqlAlchemyDatabase(_shared_sessionmaker()())
    db.save_approval(_approval())
    assert db.claim_approval_for_execution(_RID) is True
    assert db.get_approval(_RID).status == ApprovalStatus.EXECUTING
    assert db.release_approval_claim(_RID) is True
    assert db.get_approval(_RID).status == ApprovalStatus.APPROVED
    assert db.release_approval_claim(_RID) is False  # 已非 EXECUTING → no-op


def test_auto_execute_rolls_back_claim_on_unfilled(monkeypatch):
    """apply_fill 返回 None(未成交) → 承认回滚为 APPROVED，不固着 EXECUTING。"""
    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())
    approval = _approval()
    db.save_approval(approval)
    # 打桩：撮合返回未成交
    monkeypatch.setattr(exec_mod.ExecutionService, "apply_fill",
                        lambda self, *a, **k: None)
    pos = WorkflowService(db)._auto_execute(
        approval, market="CN", profile=get_profile("CN"), live_price=10.0)
    assert pos is None
    assert db.get_approval(_RID).status == ApprovalStatus.APPROVED, "未成交应回滚为 APPROVED"


def test_auto_execute_marks_approval_executed_on_fill():
    """成交后承认应到达终态 EXECUTED（此前 save_approval 终态守卫拦截 EXECUTING→EXECUTED，
    致全系统 0 个 EXECUTED、已成交承认永卡 EXECUTING 计入 sim_max_positions）。"""
    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())
    approval = _approval()
    db.save_approval(approval)
    pos = WorkflowService(db)._auto_execute(
        approval, market="CN", profile=get_profile("CN"), live_price=10.0)
    assert pos is not None, "市价单应成交"
    assert db.get_approval(_RID).status == ApprovalStatus.EXECUTED, "成交后应为 EXECUTED"


def test_auto_execute_syncs_plan_status_executed():
    """成交后计划状态应同步为 EXECUTED（此前盘中 _auto_execute 漏同步，已成交计划永挂
    APPROVED，虚高「待执行计划」统计）。"""
    from datetime import UTC, datetime

    from zhice_os_v2.infrastructure.db import models as orm
    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())
    approval = _approval()  # order_intent_id == _PLAN_ID
    db.save_approval(approval)
    # sqlite 下 v2_trade_plan.id(BigInteger)不自增，显式插入 ORM 行（plan_status=APPROVED）
    sess = Session()
    sess.add(orm.TradePlanModel(
        id=1, plan_id=_PLAN_ID, candidate_id="c1", strategy_version_id="sv-x",
        market="CN", ts_code="000001.SZ", plan_status="APPROVED",
        created_at=datetime(2026, 9, 2, 9, 25, tzinfo=UTC)))
    sess.commit()
    sess.close()
    pos = WorkflowService(db)._auto_execute(
        approval, market="CN", profile=get_profile("CN"), live_price=10.0)
    assert pos is not None
    row = Session().query(orm.TradePlanModel).filter_by(plan_id=_PLAN_ID).one()
    assert row.plan_status == "EXECUTED", "成交后计划应 EXECUTED"


def test_mark_approval_executed_only_from_executing():
    """mark_approval_executed 只在 EXECUTING 态生效（幂等、防越权）。"""
    db = SqlAlchemyDatabase(_shared_sessionmaker()())
    db.save_approval(_approval())  # APPROVED
    assert db.mark_approval_executed(_RID) is False  # 非 EXECUTING → no-op
    assert db.claim_approval_for_execution(_RID) is True  # → EXECUTING
    assert db.mark_approval_executed(_RID) is True  # → EXECUTED
    assert db.get_approval(_RID).status == ApprovalStatus.EXECUTED
    assert db.mark_approval_executed(_RID) is False  # 已终态 → no-op
