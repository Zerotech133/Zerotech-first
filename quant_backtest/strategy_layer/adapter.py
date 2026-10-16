# -*- coding: utf-8 -*-
"""策略适配器协议与注册表。

:class:`StrategyAdapter` 是全部策略适配器必须满足的 Protocol：
- 类属性 ``strategy_id: str`` 策略唯一 id（如 kg_boost / chan_monitor）
- ``score(trade_date: str) -> list[Signal]``：给定交易日返回信号列表；
  任何内部失败一律降级返回空列表（降级≠失败语义），绝不 raise 到调用方。

:class:`Registry` 维护适配器注册、启用开关与权重，开关持久化到
``strategy_layer/strategy_registry.json``::

    {"enabled": ["kg_boost", "chan_monitor"],
     "weights": {"kg_boost": 0.5, "chan_monitor": 0.5}}

持久化语义：
- 文件缺失 / JSON 损坏 / 结构不符 → 回退默认状态：全部启用、均权
  （均权 = 1/当前注册数，两个策略即 0.5/0.5）。
- 写文件失败 → 打印日志，不影响内存状态。
- 文件中已有的开关/权重在对应策略 register 时套用；文件未提及的
  策略用默认值（启用 + 均权）。
"""
import json
import os

from typing import Dict, List, Optional, Protocol, runtime_checkable

from .signal import Signal

# 开关持久化文件默认路径（与本文件同目录）
DEFAULT_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "strategy_registry.json")


@runtime_checkable
class StrategyAdapter(Protocol):
    """策略适配器协议：策略 id + 打分入口。"""

    strategy_id: str

    def score(self, trade_date: str) -> List[Signal]:
        """给定交易日（YYYYMMDD），返回标准信号列表。

        实现方约定：内部失败降级返回 []，不向调用方抛异常。
        """
        ...  # pragma: no cover


class Registry:
    """策略注册表：注册 / 查询 / 启用开关 / 权重，开关落盘持久化。

    Args:
        store_path: 持久化 JSON 路径，默认 ``strategy_layer/strategy_registry.json``；
            测试可传入临时路径隔离。
    """

    def __init__(self, store_path: str = DEFAULT_STORE_PATH) -> None:
        self._store_path = store_path
        self._adapters: Dict[str, StrategyAdapter] = {}
        self._enabled: Dict[str, bool] = {}
        # 权重表：显式设置过存数值；未设置存 None（自动均权 1/注册数）
        self._weights: Dict[str, Optional[float]] = {}
        self._saved_enabled: Optional[List] = None
        self._saved_weights: Optional[Dict] = None
        self._saved_top_n: Optional[Dict] = None
        self._load()

    # ------------------------------------------------------------------ #
    # 注册与查询
    # ------------------------------------------------------------------ #
    def register(self, adapter: StrategyAdapter,
                 enabled: Optional[bool] = None,
                 weight: Optional[float] = None) -> None:
        """注册一个适配器。

        - 首次注册：开关/权重取显式参数；未给参数时优先套用持久化
          文件中的值，文件也没有则默认「启用 + 均权 1/注册数」。
        - 同 id 重复注册：覆盖实例，保留既有开关与权重。
        - 注册后尽力落盘；落盘失败不影响内存状态。
        """
        sid = getattr(adapter, "strategy_id", None)
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("adapter 缺少合法的 strategy_id")

        if sid in self._adapters:
            # 覆盖注册：只换实例，保留开关与权重
            self._adapters[sid] = adapter
            self._save()
            return

        self._adapters[sid] = adapter
        if enabled is None:
            enabled = self._saved_enabled_for(sid, default=True)
        if weight is None:
            weight = self._saved_weight_for(sid)
        self._enabled[sid] = bool(enabled)
        # 权重为 None 表示「自动均权」，由 get_weight()/weights 动态折算
        self._weights[sid] = None if weight is None else float(weight)
        self._save()

    def get(self, strategy_id: str) -> StrategyAdapter:
        """按 id 取适配器；未注册抛 :class:`KeyError`。"""
        if strategy_id not in self._adapters:
            raise KeyError(f"策略未注册: {strategy_id}")
        return self._adapters[strategy_id]

    def list_ids(self) -> List[str]:
        """全部已注册策略 id（按注册顺序）。"""
        return list(self._adapters.keys())

    def enabled_ids(self) -> List[str]:
        """当前启用的策略 id 子集（按注册顺序）。"""
        return [sid for sid in self.list_ids() if self._enabled.get(sid, False)]

    def is_enabled(self, strategy_id: str) -> bool:
        """查询某策略是否启用；未注册返回 False。"""
        return bool(self._enabled.get(strategy_id, False))

    def get_weight(self, strategy_id: str) -> float:
        """查询某策略权重；未显式设置的用均权 1/注册数；未注册返回 0.0。"""
        w = self._weights.get(strategy_id)
        if w is None:
            if strategy_id not in self._adapters:
                return 0.0
            return 1.0 / max(len(self._adapters), 1)
        return float(w)

    @property
    def weights(self) -> Dict[str, float]:
        """全部已注册策略的有效权重表（自动均权折算后）。"""
        return {sid: self.get_weight(sid) for sid in self.list_ids()}

    # ------------------------------------------------------------------ #
    # 开关与权重
    # ------------------------------------------------------------------ #
    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        """设置启用开关；未注册抛 :class:`KeyError`。"""
        if strategy_id not in self._adapters:
            raise KeyError(f"策略未注册: {strategy_id}")
        self._enabled[strategy_id] = bool(enabled)
        self._save()

    def set_weight(self, strategy_id: str, weight: float) -> None:
        """设置权重（>=0）；未注册抛 :class:`KeyError`。"""
        if strategy_id not in self._adapters:
            raise KeyError(f"策略未注册: {strategy_id}")
        if weight < 0:
            raise ValueError(f"weight 不能为负，收到: {weight}")
        self._weights[strategy_id] = float(weight)
        self._save()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _saved_enabled_for(self, strategy_id: str, default: bool) -> bool:
        """从已加载的持久化状态取开关；无状态/未提及用 default。"""
        if self._saved_enabled is None:
            return default
        return strategy_id in self._saved_enabled

    def _saved_weight_for(self, strategy_id: str) -> Optional[float]:
        """从已加载的持久化状态取权重；无状态/未提及返回 None（自动均权）。"""
        if self._saved_weights is not None and strategy_id in self._saved_weights:
            return float(self._saved_weights[strategy_id])
        return None

    def _saved_top_n_for(self, strategy_id: str) -> Optional[int]:
        """从持久化状态取 top_n；未配置/无效值返回 None（不截断）。"""
        if self._saved_top_n is not None and strategy_id in self._saved_top_n:
            try:
                n = int(self._saved_top_n[strategy_id])
                return n if n > 0 else None
            except (TypeError, ValueError):
                return None
        return None

    def top_n(self, strategy_id: str) -> Optional[int]:
        """单策略 top-N 截断配置（P2-20261017）；None = 不截断。"""
        return self._saved_top_n_for(strategy_id)

    def set_top_n(self, strategy_id: str, n: Optional[int]) -> None:
        """设置/清除（None）单策略截断数；立即落盘（尽力）。"""
        if self._saved_top_n is None:
            self._saved_top_n = {}
        if n is None:
            self._saved_top_n.pop(strategy_id, None)
        else:
            self._saved_top_n[strategy_id] = int(n)
        self._save()

    def _load(self) -> None:
        """启动时读持久化文件。

        文件缺失 → 静默（首次运行，用默认状态）。
        JSON 损坏 / 结构不符 → 打印日志，回退默认（全开均权）。
        """
        if not os.path.exists(self._store_path):
            return
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("registry store 顶层必须为 dict")
            enabled = data.get("enabled", [])
            weights = data.get("weights", {})
            if not isinstance(enabled, list) or not isinstance(weights, dict):
                raise ValueError("enabled 必须为 list，weights 必须为 dict")
            self._saved_enabled = [str(x) for x in enabled]
            self._saved_weights = {str(k): float(v)
                                   for k, v in weights.items()}
            top_n = data.get("top_n", {})
            self._saved_top_n = (dict(top_n)
                                 if isinstance(top_n, dict) else {})
        except Exception as exc:  # JSON 缺失/坏 → 默认全开均权
            print(f"[strategy_layer] registry store 加载失败，回退默认"
                  f"(全开均权): {exc}")
            self._saved_enabled = None
            self._saved_weights = None
            self._saved_top_n = None

    def _save(self) -> None:
        """尽力落盘当前开关与权重；写失败仅打印日志，不影响内存状态。"""
        payload = {
            "enabled": self.enabled_ids(),
            "weights": {sid: self.get_weight(sid)
                        for sid in self.list_ids()},
            "top_n": {sid: n for sid, n in (self._saved_top_n or {}).items()
                      if n is not None},
        }
        try:
            d = os.path.dirname(self._store_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[strategy_layer] registry store 写入失败"
                  f"(内存状态不受影响): {exc}")


def bootstrap_default_adapters(registry: Registry) -> Registry:
    """把内置策略适配器注册进 registry（幂等）。

    背景：fresh 进程 ``Registry()`` 的 ``_adapters`` 为空（适配器在
    运行时 register），每日编排器 / 服务进程在调 score 前必须先调用
    本助手完成注册引导，否则 ``enabled_ids()`` 是空集。

    - 内置策略：kg_boost / chan_monitor；
    - 幂等：已注册的同 id 策略跳过，不覆盖既有开关/权重；
    - 开关/权重沿用 Registry.register 的默认语义（持久化文件优先，
      否则启用 + 均权）。

    adapter 以函数内懒 import 引入，避免 adapter.py ↔ adapters 子包
    在模块加载期形成循环依赖。
    """
    from .adapters.chan_monitor import ChanMonitorAdapter
    from .adapters.kg_boost import KgBoostAdapter

    for make in (KgBoostAdapter, ChanMonitorAdapter):
        adapter = make()
        if adapter.strategy_id not in registry.list_ids():
            registry.register(adapter)
    return registry
