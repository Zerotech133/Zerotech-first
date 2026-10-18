"""按 market 的模拟账户初始资金(本位币) —— 净值/风控净值真源,禁跨币种求和。"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.config.settings import get_settings


def account_initial_capital(market: str = "CN") -> Decimal:
    """返回该市场模拟账户初始资金(本位币金额)。未知市场回退 CN。"""
    if market == "US":
        return Decimal(str(get_settings().sim_initial_capital_us))
    if market == "HK":
        return Decimal(str(get_settings().sim_initial_capital_hk))
    if market == "CRYPTO":
        return Decimal(str(get_settings().sim_initial_capital_crypto))
    return Decimal(str(get_settings().sim_initial_capital))
