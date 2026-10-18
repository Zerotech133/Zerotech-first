"""持仓操作服务测试：加仓（纪律）/部分减仓（拆分 lot）/分批止盈。"""
from decimal import Decimal

from zhice_os_v2.application.services.position_ops_service import PositionOpsService
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SV = "sv-1"
MARKET = "CN"
CODE = "600519.SH"


def _make_db_with_lot(position_id: str = "pos-1", qty: str = "100", price: str = "10") -> InMemoryDatabase:
    db = InMemoryDatabase()
    db.save_position(PositionLot(
        position_id=position_id,
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal(price),
    ))
    return db


# ---------- add_position ----------

def test_add_position_creates_new_lot():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.add_position(SV, MARKET, CODE, qty=Decimal("50"), price=Decimal("11"))
    assert result["ok"] is True
    assert result["position_id"] is not None
    added = db.positions[result["position_id"]]
    assert added.open_qty == Decimal("50")
    assert added.open_price == Decimal("11")
    assert added.status == PositionStatus.OPEN
    # 原 lot 不变（批次制）
    assert db.positions["pos-1"].open_qty == Decimal("100")


def test_add_position_rejects_averaging_down():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.add_position(SV, MARKET, CODE, qty=Decimal("50"), price=Decimal("9"), last_fill_price=Decimal("10"))
    assert result["ok"] is False
    assert result["position_id"] is None
    assert result["reason"] == "averaging_down_forbidden"
    assert len(db.positions) == 1


def test_add_position_max_two_lots():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    first = svc.add_position(SV, MARKET, CODE, qty=Decimal("50"), price=Decimal("11"))
    assert first["ok"] is True
    second = svc.add_position(SV, MARKET, CODE, qty=Decimal("50"), price=Decimal("12"))
    assert second["ok"] is False
    assert second["reason"] == "max_entries_reached"
    assert len(db.positions) == 2


# ---------- partial_close ----------

def test_partial_close_splits_lot():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.partial_close("pos-1", close_qty=Decimal("30"), close_price=Decimal("12"))
    assert result["ok"] is True
    assert result["realized_pnl"] == Decimal("60")  # (12-10)*30
    assert result["remaining_qty"] == Decimal("70")
    # 原 lot 扣减且保持 OPEN
    original = db.positions["pos-1"]
    assert original.open_qty == Decimal("70")
    assert original.status == PositionStatus.OPEN
    # 平仓留痕 lot
    closed = db.positions["pos-1-c1"]
    assert closed.status == PositionStatus.CLOSED
    assert closed.open_qty == Decimal("30")
    assert closed.close_qty == Decimal("30")
    assert closed.close_price == Decimal("12")
    assert closed.closed_at is not None


def test_partial_close_full_closes_position():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.partial_close("pos-1", close_qty=Decimal("100"), close_price=Decimal("11"))
    assert result["ok"] is True
    assert result["realized_pnl"] == Decimal("100")  # (11-10)*100
    assert result["remaining_qty"] == Decimal("0")
    lot = db.positions["pos-1"]
    assert lot.status == PositionStatus.CLOSED
    assert lot.close_qty == Decimal("100")
    assert lot.close_price == Decimal("11")
    assert lot.closed_at is not None
    # 整仓平不产生留痕 lot
    assert "pos-1-c1" not in db.positions


def test_partial_close_rejects_invalid_qty():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    for bad in (Decimal("0"), Decimal("-5"), Decimal("150")):
        result = svc.partial_close("pos-1", close_qty=bad, close_price=Decimal("11"))
        assert result["ok"] is False, f"qty={bad} should be rejected"
        assert result["reason"] == "invalid_close_qty"
    # 非法价格
    result = svc.partial_close("pos-1", close_qty=Decimal("10"), close_price=Decimal("0"))
    assert result["ok"] is False
    assert result["reason"] == "invalid_close_price"
    # 未知持仓
    result = svc.partial_close("pos-x", close_qty=Decimal("10"), close_price=Decimal("11"))
    assert result["ok"] is False
    assert result["reason"] == "position_not_found"
    assert db.positions["pos-1"].open_qty == Decimal("100")


# ---------- tiered_take_profit ----------

def test_tiered_tp_t1_at_10pct():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.tiered_take_profit("pos-1", current_price=Decimal("11"))
    assert result["action"] == "T1"
    assert result["closed_qty"] == Decimal("50")
    assert result["realized_pnl"] == Decimal("50")  # (11-10)*50
    assert db.positions["pos-1"].open_qty == Decimal("50")
    assert db.positions["pos-1"].status == PositionStatus.OPEN
    assert "pos-1-c1" in db.positions


def test_tiered_tp_t2_at_20pct():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.tiered_take_profit("pos-1", current_price=Decimal("12"))
    assert result["action"] == "T2"
    assert result["closed_qty"] == Decimal("100")
    assert result["realized_pnl"] == Decimal("200")  # (12-10)*100
    lot = db.positions["pos-1"]
    assert lot.status == PositionStatus.CLOSED
    assert lot.close_qty == Decimal("100")


def test_tiered_tp_none_below_threshold():
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    result = svc.tiered_take_profit("pos-1", current_price=Decimal("10.5"))
    assert result["action"] == "NONE"
    assert result["closed_qty"] == Decimal("0")
    assert result["realized_pnl"] == Decimal("0")
    assert db.positions["pos-1"].open_qty == Decimal("100")


def test_tiered_tp_t2_after_t1():
    """T1 平 50% 后，涨到 20% 平剩余全部（档位跟踪）。"""
    db = _make_db_with_lot()
    svc = PositionOpsService(db)
    t1 = svc.tiered_take_profit("pos-1", current_price=Decimal("11"))
    assert t1["action"] == "T1"
    # 10% 档位已执行，仍在 10%~20% 区间不再触发
    again = svc.tiered_take_profit("pos-1", current_price=Decimal("11.5"))
    assert again["action"] == "NONE"
    # 到 20% 平剩余
    t2 = svc.tiered_take_profit("pos-1", current_price=Decimal("12"))
    assert t2["action"] == "T2"
    assert t2["closed_qty"] == Decimal("50")
    assert t2["realized_pnl"] == Decimal("100")  # (12-10)*50
    assert db.positions["pos-1"].status == PositionStatus.CLOSED
    # 已全平后再调用不再动作
    after = svc.tiered_take_profit("pos-1", current_price=Decimal("13"))
    assert after["action"] == "NONE"
