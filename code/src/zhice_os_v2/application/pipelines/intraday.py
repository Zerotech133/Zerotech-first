"""日内交易流水线定义。

每个步骤都是独立可观测、可单独重跑的用例。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

PipelineStep = Callable[..., dict]


@dataclass(frozen=True)
class Pipeline:
    name: str
    steps: tuple[str, ...]


INTRADAY_PIPELINE = Pipeline(
    name="intraday",
    steps=(
        "capture_snapshots",
        "refresh_universe_snapshots",
        "evaluate_entry_signals",
        "evaluate_spec_exits",
        "evaluate_static_exits",
        "execute_orders",
        "update_positions",
        "publish_market_snapshot",
        "send_alerts",
    ),
)
