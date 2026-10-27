# -*- coding: utf-8 -*-
"""经典四策略 S2 Agent 化插件包（RC-1 第一批 S2，s2-classic 工位）。

四插件（strategy_id 与 strategies.REGISTRY 键名一致，MANIFEST_GUIDE §1）：
- MaCrossAgent      (ma_cross)
- RsiReversalAgent  (rsi_reversal)
- BollBreakoutAgent (boll_breakout)
- TrendFollowAgent  (trend_follow)

装配条目（module/class/strategy_id）见同目录 ASSEMBLY_DRAFT.md——
待 S3 回放对账过 + S4 影子期满才可进正式装配（S5 授权步骤，本包不注册）。
"""

from .classic_agents import (
    BollBreakoutAgent,
    ClassicStrategyAgent,
    MaCrossAgent,
    RsiReversalAgent,
    TrendFollowAgent,
)

__all__ = [
    "ClassicStrategyAgent",
    "MaCrossAgent",
    "RsiReversalAgent",
    "BollBreakoutAgent",
    "TrendFollowAgent",
]
