"""MethodRegistry：显式注册（YAGNI，不做目录扫描）。

新增方法 = 子类实现 + 在 create_default_registry() 注册一行。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)
from zhice_os_v2.agent_platform.methods.chan_method import ChanMethod
from zhice_os_v2.agent_platform.methods.event_window import EventWindowMethod
from zhice_os_v2.agent_platform.methods.dual_tf_pullback import DualTfPullbackMethod
from zhice_os_v2.agent_platform.methods.rsi_spec_method import RsiSpecMethod
from zhice_os_v2.agent_platform.methods.sentiment_regime import (
    SentimentRegimeMethod,
    SentimentTracker,
    fetch_sentiment_snapshot,
)
from zhice_os_v2.agent_platform.methods.trend_break import TrendBreakMethod
from zhice_os_v2.agent_platform.methods.volume_method import VolumeMethod

logger = logging.getLogger(__name__)

__all__ = [
    "MethodRegistry",
    "WatchMethod",
    "MethodVote",
    "WatchContext",
    "ChanMethod",
    "ChanStructureAlias",
    "VolumeMethod",
    "RsiSpecMethod",
    "EventWindowMethod",
    "DualTfPullbackMethod",
    "SentimentRegimeMethod",
    "SentimentTracker",
    "TrendBreakMethod",
    "fetch_sentiment_snapshot",
    "create_default_registry",
    "create_cn_registry",
    "default_registry",
]


class MethodRegistry:
    """方法注册器：显式注册、按 id 取用、权重批量加载。"""

    def __init__(self) -> None:
        self._methods: dict[str, WatchMethod] = {}

    def register(self, method: WatchMethod) -> WatchMethod:
        if not method.method_id:
            raise ValueError("method_id 不能为空")
        if method.method_id in self._methods:
            raise ValueError(f"方法重复注册: {method.method_id}")
        self._methods[method.method_id] = method
        return method

    def all(self) -> list[WatchMethod]:
        return list(self._methods.values())

    def by_id(self, method_id: str) -> WatchMethod | None:
        return self._methods.get(method_id)

    def ids(self) -> list[str]:
        return list(self._methods.keys())

    def __len__(self) -> int:
        return len(self._methods)

    def __iter__(self) -> Iterator[WatchMethod]:
        return iter(self._methods.values())

    def load_weights(self, weights: dict[str, float]) -> None:
        """毕业权重批量更新（GraduationService → 闭环合拢入口）。"""
        for mid, w in weights.items():
            m = self._methods.get(mid)
            if m is not None:
                m.set_weight(w)

    def evaluate_all(self, ctx: WatchContext) -> list[MethodVote]:
        """扇出全部方法（单进程顺序执行——纯 CPU，开销可控）。

        出票即盖章 method.weight → MethodVote.weight：RegimeArbiter 加权表决
        从票上读权重，load_weights/GraduationService 更新由此贯通仲裁（闭环合拢）。
        """
        votes = []
        for m in self._methods.values():
            vote = m.evaluate(ctx)
            vote.weight = m.weight
            votes.append(vote)
        return votes

    def evaluate_subset(self, ctx: WatchContext, method_ids: list[str],
                        weights: dict[str, float] | None = None) -> list[MethodVote]:
        """按玩法子集评估（T06 playbook 消费；不破坏 evaluate_all 语义）。

        - method_ids：玩法声明的方法 id（未注册的自动跳过并 debug 留痕）
        - weights：玩法内权重（如 {chan: 1.5}）；缺省用注册表现值
          （毕业权重不被打法覆盖——毕业闭环优先）。
        """
        weights = weights or {}
        votes = []
        for mid in method_ids:
            m = self._methods.get(mid)
            if m is None:
                logger.debug("方法 %s 未注册（预留 id）— 跳过投票", mid)
                continue
            vote = m.evaluate(ctx)
            vote.weight = float(weights.get(mid, m.weight))
            votes.append(vote)
        return votes


class ChanStructureAlias(ChanMethod):
    """chan_structure 别名（T07）：继承 chan 完整语义，仅换 method_id/display_name。

    org_style_cn.yaml 的 chan_structure 声明由此转正——票 method_id=chan_structure，
    权重/毕业按独立 id 归因（与 chan 实例解耦）。
    """

    method_id = "chan_structure"
    display_name = "缠论结构确认"


def create_cn_registry() -> MethodRegistry:
    """CN 专用方法注册器，额外包含双周期回踩聚合方法。

    基于当前 default_registry 的方法类型复制，尊重测试/老进程对默认注册器的替换，
    且不污染全局单例。
    """
    base = default_registry()
    reg = MethodRegistry()
    for method in base.all():
        reg.register(method.__class__())
    if reg.by_id(DualTfPullbackMethod.method_id) is None:
        reg.register(DualTfPullbackMethod())
    return reg


def create_default_registry() -> MethodRegistry:
    """全局旧兼容注册器；保持旧方法集合，不含 CN 专用方法。"""
    reg = MethodRegistry()
    reg.register(ChanMethod())
    reg.register(VolumeMethod())
    reg.register(RsiSpecMethod())
    # T07：org_style_cn 满编（sentiment_regime/trend_break/event_window/chan_structure）
    reg.register(SentimentRegimeMethod())
    reg.register(TrendBreakMethod())
    reg.register(EventWindowMethod())
    reg.register(ChanStructureAlias())
    return reg


_DEFAULT: MethodRegistry | None = None


def default_registry() -> MethodRegistry:
    """进程级默认注册器（幂等）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = create_default_registry()
    return _DEFAULT
