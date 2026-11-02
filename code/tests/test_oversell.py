"""超持仓卖出测试：SELL 数量超过当前持仓时不静默丢弃。

覆盖场景：
1. 持仓100股，尝试卖出150股 → 100股正常平仓，apply_fill 返回 None，warning 已记录
2. 持仓为空，尝试卖出 → nothing closed，apply_fill 返回 None，不崩溃
3. 精确平仓（持仓100，卖出100）→ 正常 CLOSED，apply_fill 返回位置（非超持仓路径）
4. ApprovalExecutionService：超持仓时 approval.status == PARTIALLY_EXECUTED，不是 EXECUTED
5. ApprovalExecutionService：精确平仓时 approval.status == EXECUTED（正常路径不受影响）
6. ApprovalExecutionService：交易日同日 lot → T+1 守卫拒绝（sell.t1_locked）

T+1 确定性说明：
- 本文件用固定 `TRADING_NOW = 2026-11-02 02:00 UTC`（= 周一 10:00 Asia/Shanghai，
  CN 交易日）作为审批链 `now`，避免用例依赖运行当天是否交易日；
- 可卖持仓用 `PREVIOUS_LOT_OPENED_AT`（3 天前=周五）构造，明确不是当日 lot；
- 同日 lot 用例显式用 `opened_at=TRADING_NOW`，由 task-39 审批链守卫按设计拒绝。
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SV = "sv-test"
MARKET = "CN"
CODE = "600519.SH"

# 固定 CN 交易日（2026-11-02 周一 10:00 CST = 02:00 UTC），避免测试随运行日漂移。
TRADING_NOW = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
# 上一交易日（周五）开仓的 lot：可卖。
PREVIOUS_LOT_OPENED_AT = TRADING_NOW - timedelta(days=3)


def _make_filled_sell(qty: str, price: str = "12") -> OrderResult:
    return OrderResult(
        order_id="sell-order-1",
        status=OrderStatus.FILLED,
        filled_qty=Decimal(qty),
        filled_price=Decimal(price),
    )


def _db_with_open_position(
    qty: str = "100",
    *,
    opened_at: datetime = PREVIOUS_LOT_OPENED_AT,
) -> InMemoryDatabase:
    """内存DB，内含一个 OPEN 持仓 lot（默认按上一交易日开仓，T+1 可卖）。"""
    db = InMemoryDatabase()
    db.save_position(PositionLot(
        position_id="pos-open-1",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal("10"),
        opened_at=opened_at,
    ))
    return db


# -----------------------------------------------------------------------
# 1. 超持仓：持仓100，卖150 → 100平仓入账，apply_fill 返回 None
# -----------------------------------------------------------------------
def test_oversell_partial_close_already_recorded(caplog):
    """超持仓时已成交的100股必须落库（CLOSED），apply_fill 返回 None（非静默成功）。"""
    db = _db_with_open_position("100")
    svc = ExecutionService(db)
    result = _make_filled_sell("150")

    with caplog.at_level("WARNING"):
        position = svc.apply_fill(result, strategy_version_id=SV, market=MARKET,
                                  ts_code=CODE, side="SELL")

    # apply_fill 必须返回 None（上层可检测超持仓）
    assert position is None, "超持仓时 apply_fill 应返回 None，通知上层"

    # 原 lot 已被 partial_close 完整关闭（100 股全平）
    lot = db.get_position("pos-open-1")
    assert lot is not None
    assert lot.status == PositionStatus.CLOSED, "已有持仓100股应被正确平仓"
    assert lot.close_qty == Decimal("100")
    assert lot.close_price == Decimal("12")

    # 超持仓 WARNING 必须出现在日志
    assert any("超持仓" in r.message for r in caplog.records), "应有超持仓 WARNING 日志"


# -----------------------------------------------------------------------
# 2. 持仓为空，尝试卖出 → 不崩溃，apply_fill 返回 None
# -----------------------------------------------------------------------
def test_oversell_no_open_positions(caplog):
    """持仓为空时，卖出应安全返回 None，不抛异常，并记录 WARNING。"""
    db = InMemoryDatabase()  # 空库，无持仓
    svc = ExecutionService(db)
    result = _make_filled_sell("100")

    with caplog.at_level("WARNING"):
        position = svc.apply_fill(result, strategy_version_id=SV, market=MARKET,
                                  ts_code=CODE, side="SELL")

    assert position is None, "无持仓时 apply_fill 应返回 None"
    assert any("超持仓" in r.message for r in caplog.records), "应有超持仓 WARNING 日志"


# -----------------------------------------------------------------------
# 3. 精确平仓（持仓100，卖100）→ 正常，apply_fill 返回 CLOSED lot
# -----------------------------------------------------------------------
def test_exact_close_returns_position():
    """精确全平时 apply_fill 应正常返回持仓 lot（不触发超持仓路径）。"""
    db = _db_with_open_position("100")
    svc = ExecutionService(db)
    result = _make_filled_sell("100")

    position = svc.apply_fill(result, strategy_version_id=SV, market=MARKET,
                               ts_code=CODE, side="SELL")

    assert position is not None, "精确平仓时 apply_fill 应返回 lot"
    assert position.status == PositionStatus.CLOSED
    assert position.close_qty == Decimal("100")


# -----------------------------------------------------------------------
# 4. ApprovalExecutionService：超持仓时 approval 标记 PARTIALLY_EXECUTED
# -----------------------------------------------------------------------
def test_approval_execution_service_oversell_marks_partially_executed():
    """超持仓卖出时 ApprovalExecutionService 应标记 PARTIALLY_EXECUTED，而非 EXECUTED。"""
    from unittest.mock import MagicMock

    from zhice_os_v2.application.services.approval_execution_service import ApprovalExecutionService
    from zhice_os_v2.domain.market.context import MarketContext
    from zhice_os_v2.domain.market.registry import get_profile

    db = _db_with_open_position("100")  # 上一交易日 lot，T+1 可卖
    execution_svc = ExecutionService(db)

    # 模拟 orchestrator：返回一个已成交的 FILLED SELL 结果
    orchestrator_mock = MagicMock()
    orchestrator_mock.execute.return_value = [
        OrderResult(
            order_id="sell-ord-2",
            status=OrderStatus.FILLED,
            filled_qty=Decimal("150"),  # 超持仓：持仓100，尝试卖150
            filled_price=Decimal("12"),
        )
    ]

    approval = ApprovalRequest(
        request_id="req-oversell-1",
        order_intent_id="plan-1",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="SELL",
        qty=150,
        price=12.0,
        reason="test oversell",
        status=ApprovalStatus.APPROVED,
    )
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator_mock, execution_svc)
    profile = get_profile(MARKET)
    ctx = MarketContext(market=MARKET, profile=profile, currency=profile.currency, tz=profile.tz)

    positions = svc.execute_approved(approval, ctx, now=TRADING_NOW)

    # 超持仓：已平100股，但50股无法平仓 → PARTIALLY_EXECUTED
    assert approval.status == ApprovalStatus.PARTIALLY_EXECUTED, (
        f"超持仓时应为 PARTIALLY_EXECUTED，实际为 {approval.status}"
    )
    # 超持仓时 apply_fill 返回 None，positions 应为空
    assert positions == [], "超持仓时 apply_fill 返回 None，positions 应为空"

    # 原持仓已被正确平仓
    lot = db.get_position("pos-open-1")
    assert lot is not None and lot.status == PositionStatus.CLOSED


# -----------------------------------------------------------------------
# 5. ApprovalExecutionService：正常卖出（精确平仓）→ EXECUTED
# -----------------------------------------------------------------------
def test_approval_execution_service_exact_sell_marks_executed():
    """精确平仓时 ApprovalExecutionService 应标记 EXECUTED（正常路径不受影响）。"""
    from unittest.mock import MagicMock

    from zhice_os_v2.application.services.approval_execution_service import ApprovalExecutionService
    from zhice_os_v2.domain.market.context import MarketContext
    from zhice_os_v2.domain.market.registry import get_profile

    db = _db_with_open_position("100")  # 上一交易日 lot，T+1 可卖
    execution_svc = ExecutionService(db)

    orchestrator_mock = MagicMock()
    orchestrator_mock.execute.return_value = [
        OrderResult(
            order_id="sell-ord-3",
            status=OrderStatus.FILLED,
            filled_qty=Decimal("100"),  # 精确平仓
            filled_price=Decimal("12"),
        )
    ]

    approval = ApprovalRequest(
        request_id="req-exact-sell-1",
        order_intent_id="plan-2",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="SELL",
        qty=100,
        price=12.0,
        reason="test exact sell",
        status=ApprovalStatus.APPROVED,
    )
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator_mock, execution_svc)
    profile = get_profile(MARKET)
    ctx = MarketContext(market=MARKET, profile=profile, currency=profile.currency, tz=profile.tz)

    positions = svc.execute_approved(approval, ctx, now=TRADING_NOW)

    assert approval.status == ApprovalStatus.EXECUTED, (
        f"精确平仓应为 EXECUTED，实际为 {approval.status}"
    )
    assert len(positions) == 1, "精确平仓时应返回1个持仓 lot"
    assert positions[0].status == PositionStatus.CLOSED


# -----------------------------------------------------------------------
# 6. ApprovalExecutionService：交易日同日 lot → T+1 守卫拒绝（正向用例）
# -----------------------------------------------------------------------
def test_approval_execution_service_same_day_lot_t1_denied():
    """交易日（固定 2026-11-02）同日开仓 lot：审批 SELL 必须在提交前被 T+1 拒绝。"""
    from unittest.mock import MagicMock

    from zhice_os_v2.application.services.approval_execution_service import (
        ApprovalExecutionService,
        SellGuardDenied,
    )
    from zhice_os_v2.domain.market.context import MarketContext
    from zhice_os_v2.domain.market.registry import get_profile

    profile = get_profile(MARKET)
    assert profile.fallback_is_open(TRADING_NOW.date()) is True, "固定测试日必须是 CN 交易日"

    db = _db_with_open_position("100", opened_at=TRADING_NOW)  # 当日 lot
    execution_svc = ExecutionService(db)

    orchestrator_mock = MagicMock()
    orchestrator_mock.execute.return_value = [_make_filled_sell("100")]

    approval = ApprovalRequest(
        request_id="req-t1-same-day-1",
        order_intent_id="plan-3",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="SELL",
        qty=100,
        price=12.0,
        reason="test same-day lot T+1 denied",
        status=ApprovalStatus.APPROVED,
    )
    db.save_approval(approval)

    svc = ApprovalExecutionService(db, orchestrator_mock, execution_svc)
    ctx = MarketContext(market=MARKET, profile=profile, currency=profile.currency, tz=profile.tz)

    with pytest.raises(SellGuardDenied) as exc:
        svc.execute_approved(approval, ctx, now=TRADING_NOW)

    assert exc.value.code == "sell.t1_locked", "同日 lot 必须返回稳定拒绝码"
    orchestrator_mock.execute.assert_not_called()
    assert approval.status == ApprovalStatus.APPROVED, "守卫拒绝不得推进 approval 状态"
    lot = db.get_position("pos-open-1")
    assert lot is not None and lot.status == PositionStatus.OPEN, "当日 lot 不得被平"
