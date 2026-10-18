"""当前可授权平台策略谓词（B1-5）：支付下单、管理员授权、前端候选三处共用同一判定。

平台策略资格 = owner 为空（平台公共）且 PUBLIC + PUBLISHED + ACTIVE 且市场在部署启用集内。
后端是资格判断权威，不依赖前端 VITE_ENABLED_MARKETS。
"""
from __future__ import annotations

from collections.abc import Collection

from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus

SUPPORTED_MARKETS: frozenset[str] = frozenset({"CN", "US", "HK", "CRYPTO"})


def deployment_enabled_markets(
    raw: str | None, *, supported: frozenset[str] = SUPPORTED_MARKETS
) -> frozenset[str]:
    """解析 settings.scheduler_markets（逗号分隔）→ 启用市场集。空=全部受支持市场。"""
    if not raw or not raw.strip():
        return frozenset(supported)
    allow = {m.strip().upper() for m in raw.split(",") if m.strip()}
    return frozenset(allow & set(supported))


def is_grantable_platform_strategy(
    strategy: Strategy, *, enabled_markets: Collection[str]
) -> bool:
    status = getattr(strategy.status, "value", strategy.status)
    return (
        strategy.owner_user_id is None
        and strategy.visibility == "PUBLIC"
        and strategy.publication_status == "PUBLISHED"
        and status == StrategyStatus.ACTIVE.value
        and strategy.market in enabled_markets
    )
