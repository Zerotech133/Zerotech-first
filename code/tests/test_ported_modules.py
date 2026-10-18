from decimal import Decimal

from zhice_os_v2.domain.execution.entry_signal import (
    evaluate_entry,
    evaluate_take_profit,
)
from zhice_os_v2.domain.execution.market_rules import (
    can_buy,
    can_sell,
    limit_down_price,
    limit_up_price,
)
from zhice_os_v2.domain.execution.position_sizing import compute_position_size
from zhice_os_v2.domain.strategy.exit_policies import get_exit_policy


def test_position_sizing_cn_lot():
    qty = compute_position_size(
        price=100.0,
        net_value=100000.0,
        max_position_ratio=0.1,
        available_cash=20000.0,
        market="CN",
    )
    assert qty % 100 == 0
    assert qty > 0


def test_position_sizing_crypto_fractional():
    qty = compute_position_size(
        price=100.0,
        net_value=100000.0,
        max_position_ratio=0.1,
        available_cash=20000.0,
        market="CRYPTO",
    )
    assert isinstance(qty, Decimal)


def test_position_sizing_hk_board_lot():
    # 港股每手异构：显式 lot_size=500 → 下单量须为 500 的整数倍（不足一手返回 0）。
    qty = compute_position_size(
        price=10.0,
        net_value=1_000_000.0,
        max_position_ratio=0.1,
        available_cash=500_000.0,
        market="HK",
        lot_size=500,
    )
    assert qty > 0
    assert qty % 500 == 0
    # 不传 lot_size → 回退 HK 画像默认（1），不按 500 取整。
    qty_default = compute_position_size(
        price=10.0,
        net_value=1_000_000.0,
        max_position_ratio=0.1,
        available_cash=500_000.0,
        market="HK",
    )
    assert qty_default > 0


def test_market_rules_cn_limit():
    assert limit_up_price(Decimal("10"), "600519.SH", "贵州茅台") == Decimal("11.00")
    assert limit_down_price(Decimal("10"), "600519.SH", "贵州茅台") == Decimal("9.00")
    ok, reason = can_buy(11.0, 10.0, "600519.SH", "贵州茅台")
    assert not ok
    ok, reason = can_sell(9.0, 10.0, "600519.SH", "贵州茅台")
    assert not ok


def test_entry_signal():
    ok, _ = evaluate_entry(10.0, 10.0, 9.9, 9.8, "10:00", market="CN")
    assert ok is True
    ok, _ = evaluate_take_profit(11.0, 10.5)
    assert ok is True


def test_exit_policy_default():
    policy = get_exit_policy(None)
    assert policy.stop_loss_pct == Decimal("0.05")
    assert policy.take_profit_pct == Decimal("0.10")
