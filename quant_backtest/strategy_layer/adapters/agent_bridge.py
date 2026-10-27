# -*- coding: utf-8 -*-
"""Agent 平台策略产出到 quant_backtest 标准信号的独立桥接适配器。

该模块只调用 Agent 的只读 ``propose`` 接口（或 Agent 平台 Registry 的
``run`` 接口），不写入任一平台账本，也不修改 Signal/Registry/Aggregator。
"""

from __future__ import annotations

import copy
import math
import os
import sys
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, List, Optional

from ..signal import Signal

try:
    from agent_platform.engine.strategy_registry import (
        StrategyAgent,
        StrategyRegistry,
        Verdict,
    )
except ImportError:
    # quant_backtest 常被单独作为工作目录运行；此时把同仓库根目录加入
    # import path，仍然复用 Agent 平台的真实类定义，而不是复制契约。
    _REPO_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from agent_platform.engine.strategy_registry import (
        StrategyAgent,
        StrategyRegistry,
        Verdict,
    )


_RECOMMENDATION_TO_ACTION = {
    "strong_buy": "buy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strong_sell": "sell",
}


class AgentSignalAdapter:
    """将一个 ``StrategyAgent`` 或一个 Agent ``StrategyRegistry`` 适配为标准策略。

    Args:
        agent: ``StrategyAgent`` 实例；也可位置传入 ``StrategyRegistry``。
        strategy_id: 使用 Registry 时必填；直接传 Agent 时默认取其 id。
        context_provider: 只读上下文提供函数，签名为
            ``context_provider(trade_date) -> Mapping``。返回的顶层映射会以
            只读视图交给 Agent。
        registry: Registry 的关键字形式；与 ``agent`` 二选一。

    ``score`` 永不把异常抛给上游，按现有 StrategyAdapter 约定打印可见的
    降级信息并返回空列表。
    """

    def __init__(
        self,
        agent: Optional[StrategyAgent] = None,
        strategy_id: Optional[str] = None,
        context_provider: Optional[Callable[[str], Mapping[str, Any]]] = None,
        *,
        registry: Optional[StrategyRegistry] = None,
    ) -> None:
        source = registry if registry is not None else agent
        if source is None:
            raise ValueError("必须提供 agent 或 registry")
        if registry is not None and agent is not None:
            raise ValueError("agent 与 registry 不能同时提供")

        self._registry = source if isinstance(source, StrategyRegistry) else None
        self._agent = source if isinstance(source, StrategyAgent) else None
        if self._registry is None and self._agent is None:
            raise TypeError("source 必须是 StrategyAgent 或 StrategyRegistry")

        if self._registry is not None:
            if not isinstance(strategy_id, str) or not strategy_id.strip():
                raise ValueError("使用 registry 时 strategy_id 必填")
            self.strategy_id = strategy_id
        else:
            agent_id = getattr(self._agent, "strategy_id", "")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent 缺少合法的 strategy_id")
            if strategy_id is not None and strategy_id != agent_id:
                raise ValueError("strategy_id 与 agent.strategy_id 不一致")
            self.strategy_id = agent_id

        if context_provider is not None and not callable(context_provider):
            raise TypeError("context_provider 必须可调用")
        self._context_provider = context_provider

    def score(self, trade_date: str) -> List[Signal]:
        """调用 Agent 生成一个 Verdict，并转换成单元素 Signal 列表。"""
        try:
            context = self._context(trade_date)
            verdict = self._run(context)
            if not isinstance(verdict, Verdict):
                raise TypeError("Agent 产出必须为 Verdict")
            verdict.validate()
            return [self._to_signal(verdict, trade_date)]
        except Exception as exc:
            print(
                f"[agent_bridge:{self.strategy_id}] 降级：打分失败，返回空信号列表: {exc}"
            )
            return []

    def _context(self, trade_date: str) -> Mapping[str, Any]:
        if self._context_provider is None:
            return MappingProxyType({})
        context = self._context_provider(trade_date)
        if not isinstance(context, Mapping):
            raise TypeError("context_provider 必须返回 Mapping")
        # 深复制后递归冻结，防止 Agent 改写调用方持有的上下文。
        return _readonly(copy.deepcopy(dict(context)))

    def _run(self, context: Mapping[str, Any]) -> Verdict:
        if self._registry is not None:
            return self._registry.run(self.strategy_id, context)
        return self._agent.propose(context)

    def _to_signal(self, verdict: Verdict, trade_date: str) -> Signal:
        action = _RECOMMENDATION_TO_ACTION[verdict.recommendation]
        raw_score = float(verdict.score)
        if not math.isfinite(raw_score):
            raise ValueError("Verdict.score 必须为有限数值")
        # Agent 平台未约束 score 的单位；交给 Signal 契约校验，非法值显式降级。
        score = raw_score
        rationale = str(verdict.rationale or verdict.recommendation)
        if verdict.trace_id:
            rationale += f" [trace_id={verdict.trace_id}]"
        if verdict.extras:
            rationale += f" [extras={verdict.extras!r}]"
        return Signal(
            symbol=verdict.symbol,
            action=action,
            score=score,
            confidence=float(verdict.confidence),
            rationale=rationale,
            strategy=verdict.strategy_id,
            trade_date=trade_date,
        )


def _readonly(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_readonly(item) for item in value)
    if isinstance(value, set):
        return frozenset(_readonly(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_readonly(item) for item in value)
    return value


__all__ = ["AgentSignalAdapter"]
