"""方法插件协议：WatchMethod ABC + MethodVote + WatchContext + 指标预计算。

对齐 zhice spec.py 信号型策略心智模型（factors+rules），输出升格为带置信度的方向票。
性能规约：逐 bar 只做"窗口切片 + 规则判断"，全部指标由向量化预计算列承载
（ctx.bars_df 已带 *_ind 预计算列时直接消费，未带时方法内首算一次整列）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

VoteDirection = Literal["SELL", "HOLD", "WAIT"]


@dataclass
class MethodVote:
    """单方法方向票：方向 + 置信度 + 人审依据 + 参数快照 + 出票时权重。

    weight 由 MethodRegistry.evaluate_all 盖章（方法实例当前毕业权重）——
    RegimeArbiter 加权表决直接读本字段；aw_method.weight / GraduationService
    闭环经此贯通到仲裁（P1 修复：票上无权重曾导致闭环整体失效）。
    """

    method_id: str
    direction: VoteDirection
    confidence: float            # [0,1]
    reasons: list[str] = field(default_factory=list)   # 人审卡片展示的依据
    params_snapshot: dict = field(default_factory=dict)  # 学习闭环回放用
    weight: float = 1.0          # 出票时方法权重（registry 盖章；手动构造默认 1.0）

    def __post_init__(self) -> None:
        if self.direction not in ("SELL", "HOLD", "WAIT"):
            raise ValueError(f"非法票方向: {self.direction!r}")
        self.confidence = float(min(1.0, max(0.0, self.confidence)))
        self.weight = float(min(3.0, max(0.3, self.weight)))   # 与 WatchMethod.set_weight 同界

    def to_dict(self) -> dict:
        return {
            "method_id": self.method_id,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "reasons": list(self.reasons),
            "params_snapshot": dict(self.params_snapshot),
            "weight": round(self.weight, 4),
        }


@dataclass
class WatchContext:
    """单标的盯盘上下文（快照尾 + 滚动窗口 + 市场状态 + 记忆提示）。

    bars_df：时间升序 OHLCV（列 dt/open/high/low/close/volume），
    可携带方法预计算列（列名 `ind_<name>`，由 ensure_indicator 惰性补齐）。

    T07 CN 扩展（全部可选，None=数据不可用，方法内降级）：
    - sentiment：市场情绪快照 {zt_count, zb_count, max_lb, red_count, amount}
    - daily_df：日线（列 date/close；MA20 趋势判定与事件兑现计算）
    - events：事件列表 [{event_date, event_type, note}]（事件窗口倒计时）
    """

    ts_code: str
    market: str
    snapshot_tail: pd.DataFrame                  # 等价线上 5s 快照语义（最近几笔）
    bars_df: pd.DataFrame                        # 滚动窗口 K 线（升序）
    regime_risk_off: bool = False                # 市场状态门（周线 risk-off）
    memory_hints: list[str] = field(default_factory=list)  # cowbridge 历史教训注入
    position_value: float = 0.0                  # 该标的持仓市值（高风险复核用）
    sentiment: dict | None = None                # T07 情绪快照（None=不可用）
    daily_df: pd.DataFrame | None = None         # T07 日线（date/close）
    events: list[dict] | None = None             # T07 事件列表
    higher_df: pd.DataFrame | None = None        # 可选高周期（通常60m；保持旧构造兼容）
    # 信号时间边界：所有输入必须满足 bar_end <= as_of_ts。
    # 置于默认字段区，保持旧位置参数构造兼容。
    signal_ts: pd.Timestamp | None = None
    as_of_ts: pd.Timestamp | None = None

    # 惰性缓存：整列向量化一次计算多次消费（线上由 Agent 预计算；回测/单测惰性补齐）
    _ind_cache: dict[str, pd.Series] = field(default_factory=dict, repr=False)
    _arr_cache: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def ensure_array(self, col: str) -> np.ndarray:
        """列 → float64 ndarray（每 ctx 只转换一次；热路径 numpy 化的关键）。"""
        arr = self._arr_cache.get(col)
        if arr is None:
            arr = self.bars_df[col].to_numpy(dtype=float)
            self._arr_cache[col] = arr
        return arr

    def ensure_raw(self, name: str, builder) -> np.ndarray:
        """ndarray 型指标缓存（builder(bars_df) -> ndarray）。"""
        arr = self._arr_cache.get(f"raw:{name}")
        if arr is None:
            arr = builder(self.bars_df)
            self._arr_cache[f"raw:{name}"] = arr
        return arr


class WatchMethod(ABC):
    """盯盘方法插件基类。新增流派 = 加一个子类 + 注册，零改仲裁层。"""

    method_id: str = ""
    display_name: str = ""
    weight: float = 1.0                  # 毕业权重（GraduationService 更新，初始 1.0）
    required_features: list[str] = []

    @abstractmethod
    def evaluate(self, ctx: WatchContext) -> MethodVote:
        """对当前窗口投票。必须：完整数据→SELL/HOLD；数据不足→WAIT(confidence=0)。"""

    def set_weight(self, w: float) -> None:
        self.weight = float(min(3.0, max(0.3, w)))   # 毕业权重界 [0.3, 3.0]

    def params(self) -> dict[str, Any]:
        """参数快照（学习闭环回放）；子类覆写补充自身参数。"""
        return {"method_id": self.method_id, "weight": round(self.weight, 4)}


def bars_window(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """取最近 n 根 K 线（升序保持）。空表安全。"""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df.tail(n)


def last_float(series: pd.Series, default: float | None = None) -> float | None:
    """Series 尾值取 float（NaN→None）；用于规则判断的标量化。"""
    if series is None or len(series) == 0:
        return default
    val = series.iloc[-1]
    if pd.isna(val):
        return default
    return float(val)
