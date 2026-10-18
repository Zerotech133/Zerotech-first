"""市场画像注册表。"""
from __future__ import annotations

from typing import cast

from zhice_os_v2.domain.market.profile import MarketProfile
from zhice_os_v2.domain.market.profiles.cn import CNProfile
from zhice_os_v2.domain.market.profiles.crypto import CryptoProfile
from zhice_os_v2.domain.market.profiles.hk import HKProfile
from zhice_os_v2.domain.market.profiles.us import USProfile

# Concrete profiles structurally satisfy MarketProfile (Protocol) but some
# method signatures differ slightly (e.g. missing default for `name`), so mypy
# cannot verify the structural match automatically — cast is safe here.
_PROFILES: dict[str, MarketProfile] = cast(
    "dict[str, MarketProfile]",
    {
        "CN": CNProfile(),
        "US": USProfile(),
        "HK": HKProfile(),
        "CRYPTO": CryptoProfile(),
    },
)


#: 全部支持的市场（用户通知偏好 market_reports_enabled 键的合法集合；Phase C Task 4）。
SUPPORTED_MARKETS: frozenset[str] = frozenset(_PROFILES)


def get_profile(market: str) -> MarketProfile:
    try:
        return _PROFILES[market]
    except KeyError:
        raise ValueError(f"unsupported market: {market}") from None
