from decimal import Decimal

from zhice_os_v2.domain.strategy.exit_policies import (
    DEFAULT_EXIT_POLICY,
    get_exit_policy,
)


def test_chan_v7_policy_values():
    p = get_exit_policy("CHAN_V7")
    assert p.stop_loss_pct == Decimal("0.05")
    assert p.take_profit_pct == Decimal("0.18")
    assert p.use_trailing is True
    assert p.trailing_activate_pct == Decimal("0.05")
    assert p.trailing_trail_pct == Decimal("0.035")
    assert p.max_hold_days == 15
    assert p.dynamic_stop == "CHAN_ZS"


def test_existing_policies_backward_compatible():
    assert get_exit_policy("CHAN").max_hold_days is None
    assert get_exit_policy("CHAN").dynamic_stop is None
    assert DEFAULT_EXIT_POLICY.max_hold_days is None
    assert DEFAULT_EXIT_POLICY.dynamic_stop is None
