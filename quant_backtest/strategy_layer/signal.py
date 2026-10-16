# -*- coding: utf-8 -*-
"""标准信号契约：全站策略统一产出的 :class:`Signal` 数据类。

字段约定（权威契约，不得改动字段名）：
- symbol      标准代码格式：000338.SZ / 600519.SH / 00700.HK
- action      buy / sell / hold / watch 四值之一（构造时校验）
- score       0-100 标准化分（构造时校验）
- confidence  0-1 置信度（构造时校验）
- rationale   人话依据，前端直接展示，必须非空（构造时校验）
- strategy    来源策略 id（如 kg_boost / chan_monitor）
- trade_date  交易日，YYYYMMDD 8 位数字（构造时校验）
"""
import re

from dataclasses import dataclass

# action 合法取值
VALID_ACTIONS = ("buy", "sell", "hold", "watch")

# trade_date 合法格式：YYYYMMDD
_TRADE_DATE_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class Signal:
    """单一策略对单一标的的标准化信号。

    构造时做全量校验，任何字段非法直接抛 :class:`ValueError`，
    保证下游拿到的 Signal 一定满足契约。
    """

    symbol: str        # 标准格式：000338.SZ / 600519.SH / 00700.HK
    action: str        # buy / sell / hold / watch（四值之一）
    score: float       # 0-100 标准化分
    confidence: float  # 0-1 置信度
    rationale: str     # 人话依据（前端直接展示，必须非空）
    strategy: str      # 来源策略 id（如 kg_boost）
    trade_date: str    # YYYYMMDD

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol 必须为非空字符串")
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"action 必须为 {VALID_ACTIONS} 之一，收到: {self.action!r}")
        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise ValueError("score 必须为数值")
        if not (0.0 <= float(self.score) <= 100.0):
            raise ValueError(f"score 必须在 0-100 区间，收到: {self.score}")
        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("confidence 必须为数值")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence 必须在 0-1 区间，收到: {self.confidence}")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale 必须为非空字符串")
        if not isinstance(self.strategy, str) or not self.strategy.strip():
            raise ValueError("strategy 必须为非空字符串")
        if not isinstance(self.trade_date, str) or not _TRADE_DATE_RE.match(self.trade_date):
            raise ValueError(f"trade_date 必须为 YYYYMMDD 格式，收到: {self.trade_date!r}")
