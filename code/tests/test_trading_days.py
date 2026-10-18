from datetime import date

from zhice_os_v2.domain.market.trading_days import count_trading_days


def test_count_trading_days_inclusive_open_days():
    cal = {
        date(2026, 1, 5): True,
        date(2026, 1, 6): True,
        date(2026, 1, 7): True,
        date(2026, 1, 8): True,
        date(2026, 1, 9): True,
        date(2026, 1, 10): False,
        date(2026, 1, 11): False,
        date(2026, 1, 12): True,
    }
    assert count_trading_days(cal, date(2026, 1, 5), date(2026, 1, 12)) == 6
    assert count_trading_days(cal, date(2026, 1, 5), date(2026, 1, 5)) == 1
    assert count_trading_days(cal, date(2026, 1, 8), date(2026, 1, 5)) == 0
