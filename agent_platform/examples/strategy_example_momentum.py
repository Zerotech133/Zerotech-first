# -*- coding: utf-8 -*-
"""只读动量 StrategyAgent 示例。"""

from strategy_registry import StrategyAgent, Verdict


WINDOW = 5


class StrategyExampleMomentumAgent(StrategyAgent):
    """读取 closes 并输出 Verdict，不执行交易或写入账本。"""

    strategy_id = "strategy_example_momentum_v1"
    version = "1.0.0"
    family = "example"
    capability_manifest = ("momentum", "readonly")
    lifecycle_state = "experimental"
    stance = "neutral"

    def propose(self, context):
        symbol = context.get("symbol", "")
        closes = [float(value) for value in (context.get("closes") or [])]
        if len(closes) < WINDOW or closes[0] <= 0:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="hold",
                confidence=0.0,
                stance="neutral",
                score=0.0,
                rationale="样本不足或起始价格非法",
            )

        momentum = closes[-1] / closes[-WINDOW] - 1.0
        if momentum > 0:
            recommendation = "buy"
            stance = "bull"
        elif momentum < 0:
            recommendation = "sell"
            stance = "bear"
        else:
            recommendation = "hold"
            stance = "neutral"

        return Verdict(
            strategy_id=self.strategy_id,
            symbol=symbol,
            recommendation=recommendation,
            confidence=round(min(abs(momentum) * 10.0, 0.95), 4),
            stance=stance,
            score=round(momentum, 6),
            rationale="%d 日动量 %+.2f%%" % (WINDOW, momentum * 100),
        )
