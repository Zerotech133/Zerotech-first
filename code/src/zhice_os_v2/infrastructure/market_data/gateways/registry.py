"""market → 数据网关工厂（复刻 V1 data_provider/registry.py）。

惰性 import：各市场网关按需加载，便于分阶段落地（当前 CN 已就绪，US/HK/CRYPTO 后续接入）。
每次返回新实例（网关有状态，用完 close）。
"""
from __future__ import annotations

from .base import DataGateway


def get_data_gateway(market: str = "CN") -> DataGateway:
    m = (market or "CN").upper()
    if m == "CN":
        from .datacenter import DataCenterGateway
        return DataCenterGateway()
    if m == "US":
        from .us import USGateway
        return USGateway()
    if m == "HK":
        from .hk import HKGateway
        return HKGateway()
    if m == "CRYPTO":
        from .crypto import CryptoGateway
        return CryptoGateway()
    raise ValueError(f"unsupported market: {market}")
