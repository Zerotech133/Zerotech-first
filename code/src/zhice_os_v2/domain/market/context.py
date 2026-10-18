"""MarketContext：应用服务获取市场能力的统一入口。"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.profile import MarketProfile


@dataclass(frozen=True)
class MarketContext:
    market: str
    profile: MarketProfile
    currency: str
    tz: ZoneInfo
    # db / calendar / data_provider / broker 后续通过端口注入
