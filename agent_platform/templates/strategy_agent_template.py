# -*- coding: utf-8 -*-
"""第一版可插拔策略 Agent 模板。

复制本文件到插件目录后，只需修改类元数据和 propose() 中的占位判断；
插件只返回 Verdict，不持有交易执行、账户或账本写入能力。
"""

from strategy_registry import StrategyAgent, Verdict


class MyStrategyAgent(StrategyAgent):
    """无真实交易逻辑的最小策略插件模板。"""

    strategy_id = "my_strategy_v1"
    version = "1.0.0"
    family = "example"
    capability_manifest = ("example_capability",)
    lifecycle_state = "experimental"
    stance = "neutral"

    def propose(self, context):
        """根据只读 context 产出占位 Verdict；不要在此处执行交易或写账本。"""
        symbol = context.get("symbol", "")
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=symbol,
            recommendation="hold",
            confidence=0.0,
            stance="neutral",
            score=0.0,
            rationale="模板占位：尚未实现策略判断",
        )
