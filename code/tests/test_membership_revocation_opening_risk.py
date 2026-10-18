"""单元 B Task B2-1：撤权时停止"新增开仓风险"（清理仓储 + 撤销完整入口）。

隔离内存 sqlite（StaticPool），同 tests/test_admin_entitlement_transactions.py env 范式。
覆盖：只停自建私有启用实例并取消其排队开仓/未提交 BUY 审批/OPEN-BUY-NEW 无提交意图；
保留已成交/已提交 request 意图/CLOSE-SELL/平台策略/他人私有；释放 RESERVED 预留
（reserved_cash 减、cash_balance 不变、状态→RELEASED、released_amount 正确）；
预留账实不符整体回滚；SimulatedOnlyBrokerCancellationOutbox 契约。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext
from zhice_os_v2.domain.strategy.strategy import StrategyStatus
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.entitlement_risk_repository import (
    EntitlementRiskRepository,
    SimulatedOnlyBrokerCancellationOutbox,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def Session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def svc(Session):
    return AdminEntitlementService(
        session_factory=lambda: Session(),
        enabled_markets=frozenset({"US", "CRYPTO"}),
        clock=lambda: NOW,
    )


def _ctx(key="rev-00000001", fp="fp-rev"):
    return AdminWriteContext(actor_user_id="admin", idempotency_key=key,
                             request_fingerprint=fp)


# ── 数据播种 ─────────────────────────────────────────────────────────

def _strategy(s, sid, owner, vis, pub):
    s.add(orm.StrategyModel(
        strategy_id=sid, strategy_name=sid, market="US",
        status=StrategyStatus.ACTIVE.value, owner_user_id=owner,
        visibility=vis, publication_status=pub, created_at=NOW, updated_at=NOW,
    ))


def _version(s, svid, sid):
    s.add(orm.StrategyVersionModel(
        strategy_version_id=svid, strategy_id=sid, version_no="1", market="US",
        status="ACTIVE", spec={}, created_at=NOW,
    ))


def _account(s, aid, uid, cash, reserved):
    s.add(orm.UserAccountModel(
        account_id=aid, user_id=uid, market="US", currency="USD",
        account_type="SHARED_PORTFOLIO", initial_capital=Decimal("100000"),
        cash_balance=Decimal(str(cash)), reserved_cash=Decimal(str(reserved)),
        status="ACTIVE", created_at=NOW, updated_at=NOW,
    ))


def _instance(s, usid, uid, svid, aid, enabled):
    s.add(orm.UserStrategyInstanceModel(
        user_strategy_id=usid, user_id=uid, strategy_version_id=svid, account_id=aid,
        enabled=enabled, auto_execute=False, execution_mode="MANUAL",
        max_position_ratio=Decimal("0.1"), created_at=NOW, updated_at=NOW,
    ))


# 显式自增 id：TradePlan/OrderIntent/OrderRequest 主键为纯 BigInteger，sqlite 下不自增。
_seq = {"n": 0}


def _next_id():
    _seq["n"] += 1
    return _seq["n"]


def _plan(s, pid, usid, aid, status):
    s.add(orm.TradePlanModel(
        id=_next_id(),
        plan_id=pid, candidate_id=f"cand-{pid}", strategy_version_id="sv-x",
        market="US", ts_code="AAA", plan_status=status, user_strategy_id=usid,
        account_id=aid, created_at=NOW,
    ))


def _approval(s, rid, usid, direction, status):
    s.add(orm.ApprovalRequestModel(
        request_id=rid, order_intent_id=f"oi-{rid}", strategy_version_id="sv-x",
        market="US", ts_code="AAA", direction=direction, qty=Decimal("1"),
        price=Decimal("10"), status=status, channel="WEB_POPUP", token="t",
        user_strategy_id=usid, created_at=NOW,
    ))


def _intent(s, iid, usid, itype, side, status, plan_id="p-x"):
    s.add(orm.OrderIntentModel(
        id=_next_id(),
        intent_id=iid, plan_id=plan_id, strategy_version_id="sv-x", market="US",
        ts_code="AAA", intent_type=itype, intent_side=side, status=status,
        user_strategy_id=usid, created_at=NOW,
    ))


def _request(s, rqid, iid, status):
    s.add(orm.OrderRequestModel(
        id=_next_id(),
        request_id=rqid, intent_id=iid, market="US", broker_channel="SIMULATED",
        idempotency_key=f"idem-{rqid}", status=status, created_at=NOW,
    ))


def _reservation(s, resid, aid, usid, idem_key, amount, status):
    s.add(orm.AccountReservationModel(
        reservation_id=resid, account_id=aid, user_strategy_id=usid,
        idempotency_key=idem_key, amount=Decimal(str(amount)), status=status,
        created_at=NOW, updated_at=NOW,
    ))


def _seed_full(Session):
    """u-t：自建私有启用实例 inst-priv + 平台实例 inst-pub + 他人私有 inst-oth +
    已停用私有 inst-off。inst-priv 下排队开仓/审批/意图/预留全套。"""
    s = Session()
    s.add(orm.UserModel(user_id="u-t", username="t", password_hash="x", role="USER",
                        is_active=True, created_at=NOW))
    s.add(orm.UserModel(user_id="u-oth", username="o", password_hash="x", role="USER",
                        is_active=True, created_at=NOW))
    _strategy(s, "strat-priv", "u-t", "PRIVATE", "PRIVATE")
    _strategy(s, "strat-pub", None, "PUBLIC", "PUBLISHED")
    _strategy(s, "strat-oth", "u-oth", "PRIVATE", "PRIVATE")
    _version(s, "sv-priv", "strat-priv")
    _version(s, "sv-pub", "strat-pub")
    _version(s, "sv-oth", "strat-oth")
    _account(s, "acc-1", "u-t", cash=10000, reserved=500)
    _instance(s, "inst-priv", "u-t", "sv-priv", "acc-1", enabled=True)
    _instance(s, "inst-pub", "u-t", "sv-pub", "acc-1", enabled=True)
    _instance(s, "inst-oth", "u-t", "sv-oth", "acc-1", enabled=True)
    _instance(s, "inst-off", "u-t", "sv-priv", "acc-1", enabled=False)
    # 计划：PENDING/APPROVED 取消；FILLED 保留。
    _plan(s, "plan-pending", "inst-priv", "acc-1", "PENDING")
    _plan(s, "plan-approved", "inst-priv", "acc-1", "APPROVED")
    _plan(s, "plan-filled", "inst-priv", "acc-1", "FILLED")
    _plan(s, "plan-pub", "inst-pub", "acc-1", "PENDING")  # 平台实例计划：不动
    # 审批：BUY-PENDING 取消；SELL-PENDING、BUY-EXECUTED 保留；
    # BUY-EXECUTING 保留（已抢先领取执行，让其自然完成，避免撤权错置已成交审计）。
    _approval(s, "appr-buy-pending", "inst-priv", "BUY", "PENDING")
    _approval(s, "appr-sell-pending", "inst-priv", "SELL", "PENDING")
    _approval(s, "appr-buy-executed", "inst-priv", "BUY", "EXECUTED")
    _approval(s, "appr-buy-executing", "inst-priv", "BUY", "EXECUTING")
    # 意图：OPEN-BUY-NEW 无 request 取消；有 SUBMITTED request 保留；CLOSE-SELL 保留。
    _intent(s, "intent-open-new", "inst-priv", "OPEN", "BUY", "NEW")
    _intent(s, "intent-open-submitted", "inst-priv", "OPEN", "BUY", "NEW")
    _intent(s, "intent-close-sell", "inst-priv", "CLOSE", "SELL", "NEW")
    _request(s, "req-submitted", "intent-open-submitted", "SUBMITTED")
    # 预留：pending 计划对应 RESERVED 释放；filled 计划对应 CONSUMED 保留。
    _reservation(s, "res-pending", "acc-1", "inst-priv", "plan-pending", 500, "RESERVED")
    _reservation(s, "res-filled", "acc-1", "inst-priv", "plan-filled", 300, "CONSUMED")
    s.commit()
    s.close()


# ── 仓储直测 ─────────────────────────────────────────────────────────

def test_cleanup_disables_private_instance_and_cancels_queued(Session):
    _seed_full(Session)
    s = Session()
    try:
        res = EntitlementRiskRepository().stop_new_opening_risk(
            s, actor_user_id="admin", target_user_id="u-t", now=NOW,
            cancellation_outbox=SimulatedOnlyBrokerCancellationOutbox(),
        )
        s.commit()
    finally:
        s.close()
    assert set(res.disabled_instance_ids) == {"inst-priv"}
    assert set(res.cancelled_plan_ids) == {"plan-pending", "plan-approved"}
    assert set(res.cancelled_approval_ids) == {"appr-buy-pending"}
    assert set(res.cancelled_intent_ids) == {"intent-open-new"}
    assert res.cancelled_request_ids == ()
    assert set(res.released_reservation_ids) == {"res-pending"}
    assert res.released_amount == Decimal("500")
    assert res.pending_external_cancellations == ()


def test_cleanup_preserves_platform_other_and_executed(Session):
    _seed_full(Session)
    s = Session()
    try:
        EntitlementRiskRepository().stop_new_opening_risk(
            s, actor_user_id="admin", target_user_id="u-t", now=NOW,
            cancellation_outbox=SimulatedOnlyBrokerCancellationOutbox(),
        )
        s.commit()
    finally:
        s.close()
    s = Session()
    try:
        def inst(usid):
            return s.query(orm.UserStrategyInstanceModel).filter_by(
                user_strategy_id=usid).one()

        assert inst("inst-priv").enabled is False
        assert inst("inst-pub").enabled is True   # 平台策略实例不停
        assert inst("inst-oth").enabled is True   # 他人私有不停

        def plan(pid):
            return s.query(orm.TradePlanModel).filter_by(plan_id=pid).one()

        assert plan("plan-filled").plan_status == "FILLED"       # 已成交保留
        assert plan("plan-pub").plan_status == "PENDING"         # 平台实例计划不动

        def appr(rid):
            return s.query(orm.ApprovalRequestModel).filter_by(request_id=rid).one()

        assert appr("appr-sell-pending").status == "PENDING"     # SELL 保留
        assert appr("appr-buy-executed").status == "EXECUTED"    # 已执行保留

        def intent(iid):
            return s.query(orm.OrderIntentModel).filter_by(intent_id=iid).one()

        assert intent("intent-open-submitted").status == "NEW"   # 已提交 request 保留
        assert intent("intent-close-sell").status == "NEW"       # CLOSE/SELL 保留
    finally:
        s.close()


def test_cleanup_releases_reservation_balance(Session):
    _seed_full(Session)
    s = Session()
    try:
        EntitlementRiskRepository().stop_new_opening_risk(
            s, actor_user_id="admin", target_user_id="u-t", now=NOW,
            cancellation_outbox=SimulatedOnlyBrokerCancellationOutbox(),
        )
        s.commit()
    finally:
        s.close()
    s = Session()
    try:
        acct = s.query(orm.UserAccountModel).filter_by(account_id="acc-1").one()
        assert Decimal(str(acct.reserved_cash)) == Decimal("0")     # 500 → 0
        assert Decimal(str(acct.cash_balance)) == Decimal("10000")  # 不变
        res_pending = s.query(orm.AccountReservationModel).filter_by(
            reservation_id="res-pending").one()
        assert res_pending.status == "RELEASED"
        res_filled = s.query(orm.AccountReservationModel).filter_by(
            reservation_id="res-filled").one()
        assert res_filled.status == "CONSUMED"  # 未被取消计划关联，保留
    finally:
        s.close()


def test_cleanup_reservation_inconsistency_raises(Session):
    """reserved_cash < 预留额 → 抛 reservation_balance_inconsistent（供整体回滚）。"""
    s = Session()
    s.add(orm.UserModel(user_id="u-b", username="b", password_hash="x", role="USER",
                        is_active=True, created_at=NOW))
    _strategy(s, "sp", "u-b", "PRIVATE", "PRIVATE")
    _version(s, "svp", "sp")
    _account(s, "acc-b", "u-b", cash=10000, reserved=200)  # < 预留额 500
    _instance(s, "inst-b", "u-b", "svp", "acc-b", enabled=True)
    _plan(s, "plan-b", "inst-b", "acc-b", "PENDING")
    _reservation(s, "res-b", "acc-b", "inst-b", "plan-b", 500, "RESERVED")
    s.commit()
    s.close()
    s = Session()
    try:
        with pytest.raises(EntitlementError, match="reservation_balance_inconsistent"):
            EntitlementRiskRepository().stop_new_opening_risk(
                s, actor_user_id="admin", target_user_id="u-b", now=NOW,
                cancellation_outbox=SimulatedOnlyBrokerCancellationOutbox(),
            )
    finally:
        s.rollback()
        s.close()


def test_simulated_outbox_contract():
    ob = SimulatedOnlyBrokerCancellationOutbox()
    assert ob.supports_durable_outbox is False
    assert ob.enqueue_in_session(
        None, target_user_id="u-t", user_strategy_ids=("inst-priv",), now=NOW
    ) == ()


# ── 服务完整入口 revoke_membership ───────────────────────────────────

def _grant_member(svc):
    svc.set_membership(context=_ctx(key="grant-0001", fp="g"), target_user_id="u-t",
                       mode="permanent", valid_until=None, reason="授予",
                       expected_version=0)


def test_revoke_membership_full_entry_revokes_and_cleans(Session, svc):
    _seed_full(Session)
    _grant_member(svc)
    r = svc.revoke_membership(context=_ctx(key="revoke-0001"), target_user_id="u-t",
                              reason="退款", expected_version=1)
    assert r.status_code == 200
    assert r.body["membership"]["effective_status"] == "revoked"
    cleanup = r.body["opening_risk_cleanup"]
    assert cleanup["disabled_instance_ids"] == ["inst-priv"]
    assert set(cleanup["cancelled_plan_ids"]) == {"plan-pending", "plan-approved"}
    assert Decimal(cleanup["released_amount"]) == Decimal("500")
    assert cleanup["pending_external_cancellations"] == []
    # 会员 + 实例停用审计（1 MEMBERSHIP + 1 USER_STRATEGY_INSTANCE）。
    s = Session()
    try:
        audits = s.query(orm.EntitlementAuditModel).filter_by(
            target_user_id="u-t", action="membership.revoke").all()
        kinds = {a.resource_type for a in audits}
        assert "MEMBERSHIP" in kinds
        assert "USER_STRATEGY_INSTANCE" in kinds
        assert s.query(orm.UserStrategyInstanceModel).filter_by(
            user_strategy_id="inst-priv").one().enabled is False
    finally:
        s.close()


def test_revoke_membership_stale_version_conflict(Session, svc):
    _seed_full(Session)
    _grant_member(svc)
    with pytest.raises(EntitlementError, match="version_conflict"):
        svc.revoke_membership(context=_ctx(key="revoke-bad1"), target_user_id="u-t",
                              reason="x", expected_version=0)
    # 冲突整体回滚：实例仍启用、预留仍冻结、无 operation。
    s = Session()
    try:
        assert s.query(orm.UserStrategyInstanceModel).filter_by(
            user_strategy_id="inst-priv").one().enabled is True
        acct = s.query(orm.UserAccountModel).filter_by(account_id="acc-1").one()
        assert Decimal(str(acct.reserved_cash)) == Decimal("500")
        assert s.query(orm.AdminOperationModel).filter_by(
            idempotency_key="revoke-bad1").count() == 0
    finally:
        s.close()


def test_revoke_membership_not_granted(Session, svc):
    _seed_full(Session)
    with pytest.raises(EntitlementError, match="membership_not_granted"):
        svc.revoke_membership(context=_ctx(key="revoke-ng01"), target_user_id="u-t",
                              reason="x", expected_version=0)


def test_revoke_membership_idempotent_replay(Session, svc):
    _seed_full(Session)
    _grant_member(svc)
    first = svc.revoke_membership(context=_ctx(key="revoke-idem", fp="same"),
                                  target_user_id="u-t", reason="x", expected_version=1)
    replay = svc.revoke_membership(context=_ctx(key="revoke-idem", fp="same"),
                                   target_user_id="u-t", reason="x", expected_version=1)
    assert replay.replayed is True
    assert replay.body == first.body
