# -*- coding: utf-8 -*-
"""策略适配层（strategy_layer）。

将异构策略信号统一为标准 :class:`Signal` 并提供注册表
:class:`Registry` 管理策略开关与权重。

包结构::

    strategy_layer/
        __init__.py          # 对外出口：Signal / StrategyAdapter / Registry
        signal.py            # 标准信号契约
        adapter.py           # StrategyAdapter Protocol + Registry
        adapters/kg_boost.py     # 知识图谱热度策略（策略 id: kg_boost）
        adapters/chan_monitor.py # 缠论信号包装策略（策略 id: chan_monitor）

用法（在 quant_backtest 目录下）::

    from strategy_layer import Registry, Signal
    from strategy_layer.adapters.kg_boost import KgBoostAdapter
"""

from .adapter import Registry, StrategyAdapter, bootstrap_default_adapters
from .signal import Signal, VALID_ACTIONS

__all__ = ["Signal", "VALID_ACTIONS", "StrategyAdapter", "Registry",
           "bootstrap_default_adapters"]
