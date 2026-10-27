# -*- coding: utf-8 -*-
"""SIM_MARKET 只读数据契约。

本模块独立于现有交易主链，只表达模拟市场快照及其组成视图。
DTO 在构造时冻结嵌套序列/映射，``to_dict`` 始终返回新的 JSON-safe 对象。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence


MODE = "SIM_MARKET"
SIM_MARKET = MODE
_ALLOWED_ADJUSTMENTS = frozenset({"none", "raw", "qfq", "hfq", "forward", "backward"})


def _parse_time(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TypeError("%s 必须是非空 ISO8601 字符串" % field_name)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("%s 不是有效的 ISO8601 时间" % field_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("%s 必须包含时区" % field_name)
    return parsed


def _validate_number(value: Any, field_name: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("%s 必须是数字" % field_name)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s 必须是有限数字" % field_name)
    if non_negative and number < 0:
        raise ValueError("%s 不能为负数" % field_name)
    return number


def _freeze(value: Any, path: str = "value") -> Any:
    """递归冻结用户传入的容器，并同时检查 JSON 可表达性。"""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("%s 必须是有限数字" % path)
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("%s 的键必须是字符串" % path)
            frozen[key] = _freeze(item, "%s.%s" % (path, key))
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, "%s[%d]" % (path, index)) for index, item in enumerate(value))
    raise TypeError("%s 含有不可 JSON 序列化的值" % path)


def _thaw(value: Any) -> Any:
    if isinstance(value, _ImmutableDTO):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _validate_mode(mode: str) -> None:
    if mode != MODE:
        raise ValueError("mode 必须为 %s" % MODE)


def _validate_adjustment(adjustment: str) -> None:
    if not isinstance(adjustment, str) or adjustment not in _ALLOWED_ADJUSTMENTS:
        raise ValueError("adjustment 必须是 %s 之一" % ", ".join(sorted(_ALLOWED_ADJUSTMENTS)))


def _validate_bool(value: Any, field_name: str) -> None:
    if type(value) is not bool:
        raise TypeError("%s 必须是 bool" % field_name)


class _ImmutableDTO:
    mode: ClassVar[str] = MODE

    def to_dict(self) -> Dict[str, Any]:
        return {item.name: _thaw(getattr(self, item.name)) for item in fields(self)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]):
        if not isinstance(payload, Mapping):
            raise TypeError("契约必须从 JSON 对象构造")
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known})

    @classmethod
    def from_json(cls, payload: str):
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True, slots=True)
class SimMarketBar(_ImmutableDTO):
    """单根模拟行情 K 线；``visible_at`` 之前不可被策略看到。"""

    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    visible_at: Optional[str] = None
    visible: bool = True
    closed: bool = True
    adjustment: str = "none"
    mode: str = MODE

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol 不能为空")
        timestamp = _parse_time(self.timestamp, "timestamp")
        if self.visible_at is not None:
            visible_at = _parse_time(self.visible_at, "visible_at")
            if visible_at < timestamp:
                raise ValueError("visible_at 不能早于 timestamp")
        else:
            visible_at = None
        prices = {
            "open": _validate_number(self.open, "open", non_negative=True),
            "high": _validate_number(self.high, "high", non_negative=True),
            "low": _validate_number(self.low, "low", non_negative=True),
            "close": _validate_number(self.close, "close", non_negative=True),
        }
        if prices["high"] < max(prices["open"], prices["close"]):
            raise ValueError("high 不能低于 open 或 close")
        if prices["low"] > min(prices["open"], prices["close"]):
            raise ValueError("low 不能高于 open 或 close")
        _validate_number(self.volume, "volume", non_negative=True)
        _validate_bool(self.visible, "visible")
        _validate_bool(self.closed, "closed")
        _validate_adjustment(self.adjustment)
        _validate_mode(self.mode)
        object.__setattr__(self, "open", prices["open"])
        object.__setattr__(self, "high", prices["high"])
        object.__setattr__(self, "low", prices["low"])
        object.__setattr__(self, "close", prices["close"])
        object.__setattr__(self, "volume", float(self.volume))


@dataclass(frozen=True, slots=True)
class PortfolioView(_ImmutableDTO):
    """模拟账户在指定时间点的只读组合视图。"""

    as_of: str
    cash: float = 0.0
    equity: float = 0.0
    positions: Mapping[str, Any] = field(default_factory=dict)
    mode: str = MODE
    visible_at: Optional[str] = None

    def __post_init__(self) -> None:
        as_of = _parse_time(self.as_of, "as_of")
        if self.visible_at is not None and _parse_time(self.visible_at, "visible_at") < as_of:
            raise ValueError("visible_at 不能早于 as_of")
        cash = _validate_number(self.cash, "cash")
        equity = _validate_number(self.equity, "equity")
        if not isinstance(self.positions, Mapping):
            raise TypeError("positions 必须是映射")
        _validate_mode(self.mode)
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "positions", _freeze(self.positions, "positions"))


@dataclass(frozen=True, slots=True)
class RiskView(_ImmutableDTO):
    """模拟账户在指定时间点的只读风险视图。"""

    as_of: str
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    drawdown: float = 0.0
    leverage: float = 0.0
    risk_flags: Sequence[str] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    mode: str = MODE
    visible_at: Optional[str] = None

    def __post_init__(self) -> None:
        as_of = _parse_time(self.as_of, "as_of")
        if self.visible_at is not None and _parse_time(self.visible_at, "visible_at") < as_of:
            raise ValueError("visible_at 不能早于 as_of")
        gross = _validate_number(self.gross_exposure, "gross_exposure", non_negative=True)
        net = _validate_number(self.net_exposure, "net_exposure")
        drawdown = _validate_number(self.drawdown, "drawdown", non_negative=True)
        leverage = _validate_number(self.leverage, "leverage", non_negative=True)
        if not isinstance(self.risk_flags, (list, tuple)):
            raise TypeError("risk_flags 必须是序列")
        if any(not isinstance(flag, str) or not flag for flag in self.risk_flags):
            raise TypeError("risk_flags 必须只包含非空字符串")
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics 必须是映射")
        _validate_mode(self.mode)
        object.__setattr__(self, "gross_exposure", gross)
        object.__setattr__(self, "net_exposure", net)
        object.__setattr__(self, "drawdown", drawdown)
        object.__setattr__(self, "leverage", leverage)
        object.__setattr__(self, "risk_flags", tuple(self.risk_flags))
        object.__setattr__(self, "metrics", _freeze(self.metrics, "metrics"))


@dataclass(frozen=True, slots=True)
class SimMarketSnapshot(_ImmutableDTO):
    """同一模拟时点可见的行情、组合和风险快照。"""

    as_of: str
    bars: Sequence[SimMarketBar] = field(default_factory=tuple)
    portfolio: Optional[PortfolioView] = None
    risk: Optional[RiskView] = None
    visible: bool = True
    closed: bool = True
    adjustment: str = "none"
    mode: str = MODE

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SimMarketSnapshot":
        if not isinstance(payload, Mapping):
            raise TypeError("契约必须从 JSON 对象构造")
        values = dict(payload)
        values["bars"] = [
            item if isinstance(item, SimMarketBar) else SimMarketBar.from_dict(item)
            for item in values.get("bars", [])
        ]
        for field_name, field_type in (("portfolio", PortfolioView), ("risk", RiskView)):
            item = values.get(field_name)
            if item is not None and not isinstance(item, field_type):
                values[field_name] = field_type.from_dict(item)
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in known})

    def __post_init__(self) -> None:
        as_of = _parse_time(self.as_of, "as_of")
        if not isinstance(self.bars, (list, tuple)):
            raise TypeError("bars 必须是序列")
        normalized_bars = []
        for bar in self.bars:
            if not isinstance(bar, SimMarketBar):
                raise TypeError("bars 只能包含 SimMarketBar")
            bar_time = _parse_time(bar.timestamp, "bar.timestamp")
            if bar_time > as_of:
                raise ValueError("bar.timestamp 不能晚于 snapshot.as_of")
            if bar.visible_at is not None and _parse_time(bar.visible_at, "bar.visible_at") > as_of:
                raise ValueError("bar.visible_at 不能晚于 snapshot.as_of")
            if bar.adjustment != self.adjustment:
                raise ValueError("snapshot adjustment 必须与所有 bars 一致")
            normalized_bars.append(bar)
        if self.portfolio is not None and not isinstance(self.portfolio, PortfolioView):
            raise TypeError("portfolio 必须是 PortfolioView")
        if self.risk is not None and not isinstance(self.risk, RiskView):
            raise TypeError("risk 必须是 RiskView")
        for view_name, view in (("portfolio", self.portfolio), ("risk", self.risk)):
            if view is not None:
                if _parse_time(view.as_of, "%s.as_of" % view_name) > as_of:
                    raise ValueError("%s.as_of 不能晚于 snapshot.as_of" % view_name)
                if view.visible_at is not None and _parse_time(
                    view.visible_at, "%s.visible_at" % view_name
                ) > as_of:
                    raise ValueError("%s.visible_at 不能晚于 snapshot.as_of" % view_name)
        _validate_bool(self.visible, "visible")
        _validate_bool(self.closed, "closed")
        _validate_adjustment(self.adjustment)
        _validate_mode(self.mode)
        object.__setattr__(self, "bars", tuple(normalized_bars))


__all__ = ["MODE", "SIM_MARKET", "SimMarketBar", "PortfolioView", "RiskView", "SimMarketSnapshot"]
