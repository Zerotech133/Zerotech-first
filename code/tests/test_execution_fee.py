"""成交费用入账测试：验证 fee 流入 PositionLot.fee，并在平仓时正确扣减净 PnL。

覆盖场景：
- BUY 成交 → fee 存入 PositionLot.fee
- SELL 整仓平 → realized_pnl = gross - buy_fee - sell_fee（净值）
- SELL 部分平 → realized_pnl 扣费，剩余 lot fee 按比例保留
- SELL FIFO 多 lot → sell_fee 按成交量比例分配
- 零费（默认兼容）→ realized_pnl 与旧行为一致（纯毛利）
"""
from __future__ import annotations

import time
from decimal import Decimal

from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.position_ops_service import PositionOpsService
from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SV = "sv-fee-test"
MARKET = "CN"
CODE = "600519.SH"


def _buy_result(order_id: str, qty: str, price: str, fee: str) -> OrderResult:
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.FILLED,
        filled_qty=Decimal(qty),
        filled_price=Decimal(price),
        fee=Decimal(fee),
    )


def _sell_result(order_id: str, qty: str, price: str, fee: str) -> OrderResult:
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.FILLED,
        filled_qty=Decimal(qty),
        filled_price=Decimal(price),
        fee=Decimal(fee),
    )


def _lot(db: InMemoryDatabase, pos_id: str, qty: str, price: str, fee: str = "0") -> PositionLot:
    lot = PositionLot(
        position_id=pos_id,
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal(price),
        fee=Decimal(fee),
    )
    db.save_position(lot)
    return lot


# ---------------------------------------------------------------------------
# BUY 路径
# ---------------------------------------------------------------------------

def test_buy_fill_stores_fee_on_lot():
    """BUY 成交后，lot.fee 应等于 OrderResult.fee。"""
    db = InMemoryDatabase()
    svc = ExecutionService(db)
    result = _buy_result("ord-001", qty="100", price="10", fee="5.50")

    lot = svc.apply_fill(result, SV, MARKET, CODE, side="BUY")

    assert lot is not None
    assert lot.fee == Decimal("5.50"), f"expected fee=5.50, got {lot.fee}"
    # 确认持久化也带 fee
    stored = db.get_position(lot.position_id)
    assert stored is not None
    assert stored.fee == Decimal("5.50")


def test_buy_fill_zero_fee_default():
    """无手续费时 lot.fee 应为 0（兼容旧行为）。"""
    db = InMemoryDatabase()
    svc = ExecutionService(db)
    result = _buy_result("ord-002", qty="200", price="15", fee="0")

    lot = svc.apply_fill(result, SV, MARKET, CODE, side="BUY")

    assert lot is not None
    assert lot.fee == Decimal("0")


# ---------------------------------------------------------------------------
# SELL 整仓平
# ---------------------------------------------------------------------------

def test_sell_full_close_deducts_both_fees():
    """整仓平：realized_pnl = gross_pnl - buy_fee - sell_fee。

    买入 100 股 @ 10, buy_fee = 5
    卖出 100 股 @ 12, sell_fee = 6
    gross_pnl = (12-10)*100 = 200
    net_pnl   = 200 - 5 - 6 = 189
    """
    db = InMemoryDatabase()
    _lot(db, "pos-a", qty="100", price="10", fee="5")

    svc = ExecutionService(db)
    sell = _sell_result("ord-003", qty="100", price="12", fee="6")
    svc.apply_fill(sell, SV, MARKET, CODE, side="SELL")

    closed_lot = db.get_position("pos-a")
    assert closed_lot is not None
    assert closed_lot.status == PositionStatus.CLOSED
    assert closed_lot.pnl_amount == Decimal("189"), f"expected net_pnl=189, got {closed_lot.pnl_amount}"
    # fee 字段更新为双边费汇总（5 buy + 6 sell = 11）
    assert closed_lot.fee == Decimal("11"), f"expected fee=11, got {closed_lot.fee}"


def test_sell_full_close_gross_pnl_without_fee():
    """零手续费时 realized_pnl 与旧毛利计算一致。"""
    db = InMemoryDatabase()
    _lot(db, "pos-b", qty="100", price="10", fee="0")

    svc = ExecutionService(db)
    sell = _sell_result("ord-004", qty="100", price="12", fee="0")
    svc.apply_fill(sell, SV, MARKET, CODE, side="SELL")

    closed_lot = db.get_position("pos-b")
    assert closed_lot is not None
    assert closed_lot.pnl_amount == Decimal("200")  # (12-10)*100，费用均为 0


# ---------------------------------------------------------------------------
# SELL 部分平
# ---------------------------------------------------------------------------

def test_sell_partial_close_proportional_buy_fee():
    """部分平仓：buy_fee 按比例分摊，剩余 lot fee 相应扣减。

    买入 100 股 @ 10, buy_fee = 10
    卖出 40 股 @ 12, sell_fee = 3
    buy_fee_portion = 10 * (40/100) = 4
    gross_pnl       = (12-10)*40 = 80
    net_pnl         = 80 - 4 - 3 = 73
    剩余 lot fee    = 10 - 4 = 6（归属 60 股的买入费）
    """
    db = InMemoryDatabase()
    _lot(db, "pos-c", qty="100", price="10", fee="10")

    ops = PositionOpsService(db)
    result = ops.partial_close("pos-c", close_qty=Decimal("40"), close_price=Decimal("12"), sell_fee=Decimal("3"))

    assert result["ok"] is True
    assert result["realized_pnl"] == Decimal("73"), f"expected 73, got {result['realized_pnl']}"

    # 留痕 lot：fee = buy_fee_portion + sell_fee = 4 + 3 = 7
    trace = db.get_position("pos-c-c1")
    assert trace is not None
    assert trace.fee == Decimal("7"), f"trace fee expected 7, got {trace.fee}"
    assert trace.pnl_amount == Decimal("73")

    # 剩余 lot：fee = 10 - 4 = 6，open_qty = 60
    remaining_lot = db.get_position("pos-c")
    assert remaining_lot is not None
    assert remaining_lot.open_qty == Decimal("60")
    assert remaining_lot.fee == Decimal("6"), f"remaining fee expected 6, got {remaining_lot.fee}"


# ---------------------------------------------------------------------------
# SELL FIFO 多 lot 手续费分配
# ---------------------------------------------------------------------------

def test_sell_fifo_multi_lot_fee_proportional():
    """FIFO 跨两个 lot 卖出，sell_fee 按量比例分配到各 lot。

    lot1: 60 股 @ 10, buy_fee = 6
    lot2: 40 股 @ 11, buy_fee = 4
    卖出 100 股 @ 13 (全量), sell_fee = 10

    lot1 分配 sell_fee = 10 * (60/100) = 6
    lot2 分配 sell_fee = 10 * (40/100) = 4

    lot1 net_pnl = (13-10)*60 - 6 - 6 = 180 - 12 = 168
    lot2 net_pnl = (13-11)*40 - 4 - 4 = 80  -  8 =  72
    """
    db = InMemoryDatabase()
    lot1 = PositionLot(
        position_id="pos-d1",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal("60"),
        open_price=Decimal("10"),
        fee=Decimal("6"),
    )
    db.save_position(lot1)

    # 保证 lot2 开仓时间晚于 lot1（FIFO 排序依据）
    time.sleep(0.001)
    lot2 = PositionLot(
        position_id="pos-d2",
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal("40"),
        open_price=Decimal("11"),
        fee=Decimal("4"),
    )
    db.save_position(lot2)

    svc = ExecutionService(db)
    sell = _sell_result("ord-005", qty="100", price="13", fee="10")
    svc.apply_fill(sell, SV, MARKET, CODE, side="SELL")

    closed1 = db.get_position("pos-d1")
    closed2 = db.get_position("pos-d2")

    assert closed1 is not None and closed1.status == PositionStatus.CLOSED
    assert closed2 is not None and closed2.status == PositionStatus.CLOSED

    assert closed1.pnl_amount == Decimal("168"), f"lot1 net_pnl expected 168, got {closed1.pnl_amount}"
    assert closed2.pnl_amount == Decimal("72"),  f"lot2 net_pnl expected 72, got {closed2.pnl_amount}"


# ---------------------------------------------------------------------------
# apply_fill 通过 ExecutionService 端到端验证
# ---------------------------------------------------------------------------

def test_apply_fill_sell_via_execution_service_net_pnl():
    """通过 ExecutionService.apply_fill SELL 接口端到端验证净 PnL 已扣费。"""
    db = InMemoryDatabase()
    _lot(db, "pos-e", qty="50", price="20", fee="5")

    svc = ExecutionService(db)
    sell = _sell_result("ord-006", qty="50", price="22", fee="4")
    svc.apply_fill(sell, SV, MARKET, CODE, side="SELL")

    # gross = (22-20)*50 = 100; net = 100 - 5 - 4 = 91
    closed = db.get_position("pos-e")
    assert closed is not None
    assert closed.pnl_amount == Decimal("91"), f"expected net_pnl=91, got {closed.pnl_amount}"
    assert closed.pnl_amount < Decimal("100"), "net_pnl 必须小于 gross_pnl（手续费已扣除）"
