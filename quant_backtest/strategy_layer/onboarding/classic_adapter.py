# -*- coding: utf-8 -*-
"""经典 (df, params) -> position 策略 → strategy_layer 适配器模板（S2）。

把 ``quant_backtest/strategies.py`` 的经典四策略（ma_cross / rsi_reversal /
boll_breakout / trend_follow，签名 ``(df, params) -> position Series(0/1)``）
包装成与 :class:`strategy_layer.adapters.chan_monitor.ChanMonitorAdapter`
同形态的 adapter，输出与 aggregator 兼容的标准 :class:`Signal` 列表。

对齐点（与既有机制兼容性）：
- 协议形态同 chan_monitor：类属性 ``strategy_id`` + ``score(trade_date) ->
  List[Signal]``，满足 ``adapter.StrategyAdapter`` Protocol（duck-type，
  不强行继承）；注册走既有 ``adapter.Registry.register``，开关/权重/截断
  沿用 ``strategy_registry.json`` 既有语义，本模板零改动。
- 仓位→信号语义：position 发生变化的帧产生信号——0→1 买（buy）、1→0 卖
  （sell），持有帧不重复产信号（聚合层同 symbol 多源融合，逐帧重发会
  稀释对手方权重；与 chan_monitor「事件型」产出对齐）。序列起点之前的
  隐含状态视为 0（与 strategies.py 各函数 ``fillna(0)`` 口径一致）。
- score/confidence：模板基准分 buy=70 / sell=30、confidence=0.5（经典
  逐帧仓位无原生置信概念；影子期 S4 用实际前向收益校准后再调）。
- as-of 数据语义：loader 只按 ``end=trade_date`` 截断（只读接口），策略
  在截断 df 上重算——与回放对账（replay_reconcile）同口径，杜绝未来函数。
- 降级语义同 chan_monitor：任何内部失败打印日志返回 []，绝不 raise 到
  调用方（aggregator 依赖该约定做逐策略降级）。

只读包装铁律：不 import、不修改 strategies.py 本体；策略函数在方法内
懒加载（模块导入期不拉起任何执行链）。

用法：
    from strategy_layer.onboarding.classic_adapter import ClassicStrategyAdapter
    ad = ClassicStrategyAdapter("ma_cross", ["600519.SH"], market="CN")
    ad.score("20261016")   # -> [Signal(...)] 或 []
"""
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..signal import Signal

# quant_backtest 根目录（本文件在 quant_backtest/strategy_layer/onboarding/ 下）
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 模板基准分（S4 影子期校准位）
DEFAULT_BUY_SCORE = 70.0
DEFAULT_SELL_SCORE = 30.0
DEFAULT_CONFIDENCE = 0.5


def _default_loader(market: str, symbols: Sequence[str],
                    start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
    """默认数据源：data_loader.load_bars（zhice-db 只读接口，不重拉数据）。

    懒 import：模块导入期不碰 DB 配置；调用期才挂上。start/end 用
    YYYY-MM-DD 形态（与 runner.py CLI 口径一致）。
    """
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    from data_loader import load_bars
    return load_bars(market, list(symbols), start, end)


def strategy_fn(name: str) -> Callable[[Any, dict], Any]:
    """按策略名取 strategies.REGISTRY 本体函数（懒加载，只读引用）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    import strategies as _strat
    try:
        return _strat.REGISTRY[name]
    except KeyError as exc:
        raise ValueError("未知经典策略: %s（可选: %s）"
                         % (name, sorted(_strat.REGISTRY))) from exc


def default_params(name: str) -> dict:
    """取 PARAM_DEFAULTS 中该策略的参数默认值（与本体 get 默认一致）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    import strategies as _strat
    return dict(_strat.PARAM_DEFAULTS.get(name, {}))


def ymd8_to_dash(trade_date: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（loader/runner SQL 口径）。"""
    d = str(trade_date).replace("-", "")
    if len(d) != 8 or not d.isdigit():
        raise ValueError("trade_date 必须为 YYYYMMDD，收到: %r" % trade_date)
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


def _ymd8(dt: Any) -> str:
    """df 索引（Timestamp/str）→ YYYYMMDD（事件键与 trade_date 同口径）。"""
    s = str(dt).replace("-", "").replace("/", "")
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    raise ValueError("无法解析交易日: %r" % (dt,))


def position_change_events(pos: Any) -> Dict[Any, int]:
    """position Series → {日期: 变化后仓位}（仅变化帧，含起点 0→1 触发）。

    序列起点之前的隐含状态视为 0；与 strategies.py fillna(0) 口径一致。
    键保留原索引形态（Timestamp）；adapter 内部落信号前经 _ymd8 归一。
    """
    events: Dict[Any, int] = {}
    prev = 0
    for dt, v in pos.items():
        cur = int(v)
        if cur != prev:
            events[dt] = cur
        prev = cur
    return events


class ClassicStrategyAdapter:
    """经典逐帧仓位策略 → 标准信号适配器（四策略共用，参数化策略名）。

    Args:
        strategy_name: strategies.REGISTRY 键名（同时作为 strategy_id）。
        symbols: 标的列表（如 ["600519.SH"]）。
        market: 市场标识（CN/US/HK/CRYPTO），透传 loader。
        params: 策略参数；None 时回落 PARAM_DEFAULTS。
        loader: 数据加载函数 ``(market, symbols, start, end) -> {symbol: df}``；
            默认 data_loader.load_bars（只读）。测试注入合成 df 工厂。
        buy_score / sell_score: 模板基准分（S4 校准位）。
        confidence: 模板基准置信（S4 校准位）。
    """

    def __init__(
        self,
        strategy_name: str,
        symbols: Sequence[str],
        market: str = "CN",
        params: Optional[dict] = None,
        loader: Optional[Callable[[str, Sequence[str], Optional[str],
                                   Optional[str]], Dict[str, Any]]] = None,
        buy_score: float = DEFAULT_BUY_SCORE,
        sell_score: float = DEFAULT_SELL_SCORE,
        confidence: float = DEFAULT_CONFIDENCE,
    ) -> None:
        if not strategy_name or not strategy_name.strip():
            raise ValueError("strategy_name 必须为非空字符串")
        if not symbols:
            raise ValueError("symbols 不能为空")
        self.strategy_id = strategy_name          # Protocol 要求的类属性口径
        self._symbols = [str(s).strip() for s in symbols if str(s).strip()]
        if not self._symbols:
            raise ValueError("symbols 全为空串")
        self._market = market
        self._params = dict(params) if params is not None else default_params(strategy_name)
        self._loader = loader or _default_loader
        self._buy_score = float(buy_score)
        self._sell_score = float(sell_score)
        self._confidence = float(confidence)
        # as-of 缓存：trade_date -> {symbol: (df, events)}（同日重查免费）
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # 主入口（StrategyAdapter Protocol）
    # ------------------------------------------------------------------ #
    def score(self, trade_date: str) -> List[Signal]:
        """给定交易日（YYYYMMDD），返回当日发生仓位变化的标的信号。

        降级语义：任何内部失败打印日志返回 []，绝不 raise 到调用方。
        """
        try:
            td8 = str(trade_date).replace("-", "")
            if len(td8) != 8 or not td8.isdigit():
                raise ValueError("trade_date 必须为 YYYYMMDD")
            events_by_symbol = self._as_of_events(td8)
            out: List[Signal] = []
            for symbol, events in events_by_symbol.items():
                pos = events.get(td8)
                if pos is None:
                    continue  # 当日无仓位变化 → 无信号
                out.append(self._to_signal(symbol, td8, pos))
            out.sort(key=lambda s: -s.score)
            return out
        except Exception as exc:
            print(f"[{self.strategy_id}] 降级：打分失败，返回空信号列表: {exc}")
            return []

    # ------------------------------------------------------------------ #
    # as-of 计算与缓存
    # ------------------------------------------------------------------ #
    def _as_of_events(self, td8: str) -> Dict[str, Dict[Any, int]]:
        """trade_date as-of 截断数据 → {symbol: {日期: 变化后仓位}}。"""
        if td8 in self._cache:
            return self._cache[td8]
        frames = self._loader(self._market, self._symbols, None,
                              ymd8_to_dash(td8)) or {}
        result: Dict[str, Dict[Any, int]] = {}
        fn = strategy_fn(self.strategy_id)
        for symbol in self._symbols:
            df = frames.get(symbol)
            if df is None or len(df) == 0:
                continue
            pos = fn(df, dict(self._params))
            result[symbol] = {_ymd8(dt): v
                              for dt, v in position_change_events(pos).items()}
        if len(self._cache) > 8:
            self._cache.clear()  # 防膨胀：只保留近期交易日
        self._cache[td8] = result
        return result

    # ------------------------------------------------------------------ #
    # 信号构造（Signal 契约字段与 chan_monitor 输出同构）
    # ------------------------------------------------------------------ #
    def _to_signal(self, symbol: str, td8: str, pos: int) -> Signal:
        if pos == 1:
            action, score = "buy", self._buy_score
            verb = "做多"
        else:
            action, score = "sell", self._sell_score
            verb = "空仓"
        rationale = ("%s：仓位切换为 %d（%s），次日开盘生效，"
                     "参数 %s") % (self.strategy_id, pos, verb, self._params)
        return Signal(
            symbol=symbol,
            action=action,
            score=round(score, 2),
            confidence=round(self._confidence, 2),
            rationale=rationale,
            strategy=self.strategy_id,
            trade_date=td8,
        )


# ---------------------------------------------------------------------- #
# 合成数据冒烟（无 DB 依赖；手动执行，不进 pytest）
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import pandas as pd

    idx = pd.date_range("2026-06-01", periods=80, freq="B")
    close = pd.Series(100.0, index=idx)
    close.iloc[20:40] = 110.0   # 一段上涨触发均线/趋势策略开仓
    close.iloc[40:] = 95.0      # 一段下跌触发平仓
    df = pd.DataFrame({"close": close, "open": close, "high": close,
                       "low": close, "volume": 1000.0})

    def fake_loader(market, symbols, start, end):
        return {s: df for s in symbols}

    last_td = idx[-1].strftime("%Y%m%d")
    for name in ("ma_cross", "rsi_reversal", "boll_breakout", "trend_follow"):
        ad = ClassicStrategyAdapter(name, ["TEST.SH"], loader=fake_loader)
        # 取该策略最后一个事件日打分（冒烟日尾段可能恰无事件）
        pos = strategy_fn(name)(df, {})
        ev = position_change_events(pos)
        td8 = (_ymd8(max(ev)) if ev else last_td)
        sigs = ad.score(td8)
        print("%s smoke: %d signals @ %s" % (name, len(sigs), td8))
        for s in sigs:
            print("  %s %s score=%s conf=%s | %s"
                  % (s.symbol, s.action, s.score, s.confidence, s.rationale))
