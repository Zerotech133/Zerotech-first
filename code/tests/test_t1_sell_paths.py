"""P0-2 审批/执行链 SELL T+1 守卫回归测试（隔离，无真实 DB/不下单）。

覆盖：
1. CN T+1：当日 lot 不可卖（`_apply_sell(enforce_sell_guard=True)` 只 FIO 昨日 lot）；
2. opened_at 缺失 fail-closed；
3. T+0 市场（CRYPTO）当日 lot 可卖；
4. owner/account 归属过滤：禁止跨账户平仓，legacy 无归属卖出不吞他人 lot；
5. 停牌/无行情/跌停 守卫（复用 can_sell）；
6. 审批执行链：守卫在 claim/broker 前拒绝（approval 保持 APPROVED、orchestrator 不被调用）；
7. 普通超卖 clamp→PARTIALLY_EXECUTED（不掩盖 T+1/归属违规）；
8. BUY 路径不受影响。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from zhice_os_v2.application.services.approval_execution_service import (
    ApprovalExecutionService,
    SellGuardDenied,
)
from zhice_os_v2.application.services.execution_service import (
    SELL_DENY_INVALID_QTY,
    SELL_DENY_OPENED_AT_MISSING,
    SELL_DENY_OWNER_MISMATCH,
    SELL_DENY_SUSPENDED,
    SELL_DENY_T1_LOCKED,
    ExecutionService,
    current_trading_date,
)
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

CN_TZ = ZoneInfo("Asia/Shanghai")
SV = "v1-cn-sv-2"
CODE = "600519.SH"
MARKET = "CN"
# 固定「交易日」：2026-11-02 周一（前一日 11-01 为周日，上一交易日 2026-10-30 周五）
NOW_MONDAY = datetime(2026, 11, 2, 10, 0, tzinfo=CN_TZ)
FRI_OPEN = datetime(2026, 10, 30, 9, 35, tzinfo=CN_TZ)
MON_OPEN = datetime(2026, 11, 2, 9, 35, tzinfo=CN_TZ)


def _lot(
    position_id: str,
    *,
    qty: str = "100",
    market: str = MARKET,
    ts_code: str = CODE,
    opened_at: datetime | None = FRI_OPEN,
    user_strategy_id: str | None = None,
    account_id: str | None = None,
    sv: str = SV,
) -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id=sv,
        market=market,
        ts_code=ts_code,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal("10"),
        opened_at=opened_at,
        user_strategy_id=user_strategy_id,
        account_id=account_id,
    )


def _sell_result(qty: str = "100", price: str = "12") -> OrderResult:
    return OrderResult(
        order_id="sell-order-test",
        status=OrderStatus.FILLED,
        filled_qty=Decimal(qty),
        filled_price=Decimal(price),
    )


def _buy_result(qty: str = "100", price: str = "10") -> OrderResult:
    return OrderResult(
        order_id="buy-order-test",
        status=OrderStatus.FILLED,
        filled_qty=Decimal(qty),
        filled_price=Decimal(price),
    )


def _approval(
    *,
    direction: str = "SELL",
    qty: float = 100,
    price: float | None = 12.0,
    market: str = MARKET,
    ts_code: str = CODE,
    user_strategy_id: str | None = None,
    account_id: str | None = None,
    request_id: str = "req-t1-test",
) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        order_intent_id="plan-t1-test",
        strategy_version_id=SV,
        market=market,
        ts_code=ts_code,
        direction=direction,
        qty=qty,
        price=price,
        reason="t1 test",
        status=ApprovalStatus.APPROVED,
        user_strategy_id=user_strategy_id,
        account_id=account_id,
    )


def _ctx(market: str = MARKET) -> MarketContext:
    profile = get_profile(market)
    return MarketContext(market=market, profile=profile, currency=profile.currency, tz=profile.tz)


# --------------------------------------------------------------------------- #
# 1. 交易日判定
# --------------------------------------------------------------------------- #
def test_current_trading_date_rolls_back_on_weekend():
    sunday = datetime(2026, 11, 1, 12, 0, tzinfo=CN_TZ)
    assert current_trading_date("CN", now=sunday).isoformat() == "2026-10-30"
    assert current_trading_date("CN", now=NOW_MONDAY).isoformat() == "2026-11-02"


# --------------------------------------------------------------------------- #
# 2. _apply_sell(enforce_sell_guard=True)：只平可卖 lot
# --------------------------------------------------------------------------- #
def test_apply_sell_enforce_never_closes_today_lot():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    svc = ExecutionService(db)

    position = svc.apply_fill(
        _sell_result("100"), strategy_version_id=SV, market=MARKET, ts_code=CODE,
        side="SELL", enforce_sell_guard=True, now=NOW_MONDAY,
    )

    assert position is not None, "昨日 lot 应可平（不能被当日 lot 干扰）"
    assert db.get_position("pos-fri").status == PositionStatus.CLOSED
    mon = db.get_position("pos-mon")
    assert mon is not None and mon.status == PositionStatus.OPEN, "当日 lot 必须保持 OPEN"
    assert mon.open_qty == Decimal("100")


def test_apply_sell_enforce_only_today_lot_returns_none():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    svc = ExecutionService(db)

    position = svc.apply_fill(
        _sell_result("100"), strategy_version_id=SV, market=MARKET, ts_code=CODE,
        side="SELL", enforce_sell_guard=True, now=NOW_MONDAY,
    )

    assert position is None, "只有当日 lot 时不可平，应返回 None"
    assert db.get_position("pos-mon").status == PositionStatus.OPEN


def test_apply_sell_enforce_fifo_order_yesterday_first():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri-a", qty="50", opened_at=FRI_OPEN))
    db.save_position(_lot("pos-fri-b", qty="50", opened_at=FRI_OPEN + timedelta(hours=4)))
    svc = ExecutionService(db)

    svc.apply_fill(
        _sell_result("50"), strategy_version_id=SV, market=MARKET, ts_code=CODE,
        side="SELL", enforce_sell_guard=True, now=NOW_MONDAY,
    )

    assert db.get_position("pos-fri-a").status == PositionStatus.CLOSED, "FIFO 先平更早批次"
    assert db.get_position("pos-fri-b").status == PositionStatus.OPEN


def test_apply_sell_enforce_opened_at_missing_fail_closed():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-null", qty="100", opened_at=None))
    svc = ExecutionService(db)

    position = svc.apply_fill(
        _sell_result("100"), strategy_version_id=SV, market=MARKET, ts_code=CODE,
        side="SELL", enforce_sell_guard=True, now=NOW_MONDAY,
    )

    assert position is None, "opened_at 缺失在 T+1 市场必须 fail-closed"
    assert db.get_position("pos-null").status == PositionStatus.OPEN


# --------------------------------------------------------------------------- #
# 3. T+0 市场当日可卖
# --------------------------------------------------------------------------- #
def test_crypto_t0_same_day_lot_sellable():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-crypto", qty="2", market="CRYPTO", ts_code="BTC/USDT",
                          opened_at=NOW_MONDAY))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market="CRYPTO", strategy_version_id=SV, ts_code="BTC/USDT", qty=2,
        price=100.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is False
    assert Decimal(guard["sellable_qty"]) == Decimal("2")

    position = svc.apply_fill(
        _sell_result("2", "100"), strategy_version_id=SV, market="CRYPTO", ts_code="BTC/USDT",
        side="SELL", enforce_sell_guard=True, now=NOW_MONDAY,
    )
    assert position is not None and position.status == PositionStatus.CLOSED


# --------------------------------------------------------------------------- #
# 4. owner/account 归属过滤
# --------------------------------------------------------------------------- #
def test_sell_guard_denies_owner_mismatch():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-u2", qty="100", user_strategy_id="u2"))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        user_strategy_id="u1", price=12.0, now=NOW_MONDAY,
    )

    assert guard["denied"] is True
    assert guard["deny_code"] == SELL_DENY_OWNER_MISMATCH
    assert Decimal(guard["sellable_qty"]) == Decimal("0")
    assert db.get_position("pos-u2").status == PositionStatus.OPEN


def test_sell_guard_allows_matching_owner_and_account():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-u1a", qty="100", user_strategy_id="u1", account_id="acc-1"))
    svc = ExecutionService(db)

    ok = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        user_strategy_id="u1", account_id="acc-1", price=12.0, now=NOW_MONDAY,
    )
    assert ok["denied"] is False and Decimal(ok["sellable_qty"]) == Decimal("100")

    bad = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        user_strategy_id="u1", account_id="acc-2", price=12.0, now=NOW_MONDAY,
    )
    assert bad["denied"] is True and bad["deny_code"] == SELL_DENY_OWNER_MISMATCH


def test_legacy_sell_does_not_consume_owned_lot():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-owned", qty="100", user_strategy_id="u2"))
    svc = ExecutionService(db)

    position = svc.apply_fill(
        _sell_result("100"), strategy_version_id=SV, market=MARKET, ts_code=CODE, side="SELL",
    )

    assert position is None, "legacy 无归属卖出不得跨账户平他人 lot"
    assert db.get_position("pos-owned").status == PositionStatus.OPEN


# --------------------------------------------------------------------------- #
# 5. 停牌/跌停 守卫（can_sell 复用）
# --------------------------------------------------------------------------- #
def test_sell_guard_suspended_when_price_missing():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100"))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        price=0.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is True
    assert guard["deny_code"] == SELL_DENY_SUSPENDED


def test_sell_guard_suspended_when_snapshot_stale():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100"))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        price=12.0, pre_close=12.0,
        snapshot_time=NOW_MONDAY - timedelta(hours=2), now=NOW_MONDAY,
    )
    assert guard["denied"] is True
    assert guard["deny_code"] == SELL_DENY_SUSPENDED
    assert "停牌" in str(guard["deny_reason"])


def test_sell_guard_denies_limit_down():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100"))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100,
        price=9.0, pre_close=10.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is True
    assert guard["deny_code"] == SELL_DENY_SUSPENDED
    assert "跌停" in str(guard["deny_reason"])


# --------------------------------------------------------------------------- #
# 6. 守卫拒绝码 / 请求量边界
# --------------------------------------------------------------------------- #
def test_sell_guard_invalid_qty():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100"))
    svc = ExecutionService(db)
    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=0, price=12.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is True and guard["deny_code"] == SELL_DENY_INVALID_QTY


def test_sell_guard_no_position():
    svc = ExecutionService(InMemoryDatabase())
    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=100, price=12.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is True
    assert guard["deny_code"] == "sell.no_position"


def test_sell_guard_mixed_today_and_yesterday_denied_not_partial():
    """请求量 > 可卖量且差额来自 T+1 锁定 → 整笔拒绝（禁止用部分成交掩盖违规）。"""
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    svc = ExecutionService(db)

    guard = svc.sell_guard(
        market=MARKET, strategy_version_id=SV, ts_code=CODE, qty=150,
        price=12.0, now=NOW_MONDAY,
    )
    assert guard["denied"] is True
    assert guard["deny_code"] == SELL_DENY_T1_LOCKED
    assert Decimal(guard["t1_locked_qty"]) == Decimal("100")
    assert Decimal(guard["effective_qty"]) == Decimal("0")


# --------------------------------------------------------------------------- #
# 7. 审批执行链：claim/broker 之前拒绝，approval 保持 APPROVED
# --------------------------------------------------------------------------- #
def test_approval_sell_cn_today_lot_denied_before_orchestrator():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=100, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == SELL_DENY_T1_LOCKED
    orchestrator.execute.assert_not_called()
    assert approval.status == ApprovalStatus.APPROVED, "守卫拒绝不得推进 approval 状态"
    assert db.get_approval(approval.request_id).status == ApprovalStatus.APPROVED
    assert db.get_position("pos-mon").status == PositionStatus.OPEN


def test_approval_sell_no_position_denied():
    db = InMemoryDatabase()
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=100, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == "sell.no_position"
    orchestrator.execute.assert_not_called()
    assert approval.status == ApprovalStatus.APPROVED


def test_approval_sell_yesterday_lot_executes():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    orchestrator.execute.return_value = [_sell_result("100")]
    approval = _approval(qty=100, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    positions = svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert approval.status == ApprovalStatus.EXECUTED
    assert len(positions) == 1 and positions[0].status == PositionStatus.CLOSED
    assert db.get_position("pos-fri").status == PositionStatus.CLOSED


def test_approval_sell_plain_oversell_clamped_and_partially():
    """仅普通超卖（无 T+1/归属锁定）：按可卖量提交，成交不足 → PARTIALLY_EXECUTED。"""
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    # 模拟 broker 按可卖量成交（100），小于请求 150
    orchestrator.execute.return_value = [_sell_result("100")]
    approval = _approval(qty=150, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert orchestrator.execute.call_args.kwargs["qty"] == Decimal("100"), "应按可卖量 clamp 提交"
    assert approval.status == ApprovalStatus.PARTIALLY_EXECUTED
    assert db.get_position("pos-fri").status == PositionStatus.CLOSED


def test_approval_sell_today_and_yesterday_request_denied_whole():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=150, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == SELL_DENY_T1_LOCKED
    orchestrator.execute.assert_not_called()
    assert db.get_position("pos-mon").status == PositionStatus.OPEN
    assert db.get_position("pos-fri").status == PositionStatus.OPEN


def test_approval_sell_owner_mismatch_denied():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-u2", qty="100", user_strategy_id="u2"))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=100, price=12.0, user_strategy_id="u1")
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == SELL_DENY_OWNER_MISMATCH
    orchestrator.execute.assert_not_called()


def test_approval_sell_opened_at_missing_denied():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-null", qty="100", opened_at=None))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=100, price=12.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == SELL_DENY_OPENED_AT_MISSING


def test_approval_sell_suspended_price_zero_denied():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-fri", qty="100", opened_at=FRI_OPEN))
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    approval = _approval(qty=100, price=0.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert exc.value.code == SELL_DENY_SUSPENDED
    orchestrator.execute.assert_not_called()


# --------------------------------------------------------------------------- #
# 8. BUY 路径不受影响
# --------------------------------------------------------------------------- #
def test_approval_buy_unaffected():
    db = InMemoryDatabase()
    execution = ExecutionService(db)
    orchestrator = MagicMock()
    orchestrator.execute.return_value = [_buy_result("100", "10")]
    approval = _approval(direction="BUY", qty=100, price=10.0)
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator, execution)
    positions = svc.execute_approved(approval, _ctx(), now=NOW_MONDAY)

    assert approval.status == ApprovalStatus.EXECUTED
    assert len(positions) == 1
    assert positions[0].open_qty == Decimal("100")


# --------------------------------------------------------------------------- #
# 9. 默认（legacy）直接 apply_fill 行为保持：不做 T+1 过滤（历史调用逐字兼容）
# --------------------------------------------------------------------------- #
def test_apply_fill_legacy_default_keeps_old_filtering():
    db = InMemoryDatabase()
    db.save_position(_lot("pos-mon", qty="100", opened_at=MON_OPEN))
    svc = ExecutionService(db)

    position = svc.apply_fill(
        _sell_result("100"), strategy_version_id=SV, market=MARKET, ts_code=CODE, side="SELL",
    )

    # legacy 路径（未显式启用守卫）保持旧行为：审批链必须显式 enforce_sell_guard=True。
    assert position is not None
    assert db.get_position("pos-mon").status == PositionStatus.CLOSED
