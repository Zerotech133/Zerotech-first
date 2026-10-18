"""策略统一描述：StrategySpec。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FactorSpec(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class RuleSpec(BaseModel):
    feature: str
    op: Literal[">", "<", ">=", "<=", "=="]
    value: float
    action: Literal["enter", "exit"] = "enter"


class RiskSpec(BaseModel):
    stop_loss: float = 0.08
    take_profit: float = 0.20
    max_position_ratio: float = 0.10


class ExecutionSpec(BaseModel):
    frequency: Literal["intraday", "daily", "weekly"] = "daily"
    direction: Literal["long", "short", "long_short"] = "long"
    max_orders_per_day: int = 10


class StrategySpec(BaseModel):
    spec_version: int
    market: str
    universe: dict[str, Any]
    # bar 周期：1m/5m/15m/30m/60m/1d。intraday 信号策略用（如 5m RSI）；缺省日线。
    timeframe: str = "1d"
    factors: list[FactorSpec] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskSpec = Field(default_factory=RiskSpec)
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    backtest_perf: dict[str, Any] = Field(default_factory=dict)
    research_ref: str | None = None
