# -*- coding: utf-8 -*-
"""经典四策略 S2 Agent 化：ClassicStrategyAgent 基类 + 四具体插件。

对齐消费端（agent_platform，只读契约底座）：
- 基类：``agent_platform/engine/strategy_registry.py::StrategyAgent``——
  子类必须实现 ``propose(context) -> Verdict``；
- 产出：``Verdict``（recommendation 五档 strong_buy/buy/hold/sell/strong_sell、
  confidence [0,1]、stance bull/bear/neutral），经 ``Verdict.validate()``
  白名单校验（``StrategyRegistry.run`` 在出口统一把守）；
- context 契约（JSON 可序列化 Mapping，``shadow_strategy_runner`` 按
  ``json.dumps`` 体积计费 max_context_bytes）::

      {"symbol": str,
       "bars": [{"date": "YYYY-MM-DD", "close": float, ...}, ...],  # 升序，末项最新
      }
      兼容回退：无 bars 时读 ``"closes": [float, ...]``（对齐
      ``shadow_strategies.py`` 的 closes 口径，仅收盘价列）；
      可选 ``"trace_id": str`` 透传 Verdict.trace_id。

  bars/closes 由调用方 as-of 截断（只读 read_context 权限），Agent 不取数、
  不写任何域账本（红线：策略只产建议，execution_allowed 恒 False）。

仓位语义映射（与 classic_adapter.position_change_events 同口径，序列起点
之前的隐含状态视为 0）：
- 0→1（新开多）→ recommendation="buy"，score=70，confidence=0.5，stance=bull
- 1→0（平多）  → recommendation="sell"，score=30，confidence=0.5，stance=bear
- 持多不变     → "hold"，score=70，confidence=0.3，stance=bull
- 持平（空仓） → "hold"，score=0，confidence=0.3，stance=neutral
分数/置信为模板基准值（S4 影子期用实际前向收益校准后再调，extras 注记
``s4_calibrate``）。经典逐帧仓位无强弱概念，不出 strong_* 档。

事件帧策略窗口纪律（rsi_reversal / boll_breakout，fail-closed）：
本体是 ``ffill`` 持有至反向事件的事件帧语义，单点 propose 拿不到全历史
状态，必须在 context Bar 窗口内全量重算（strategies 函数本身就是从 Bar
序列全量重算，ffill 在窗口内自洽）。但窗口起点截断会带来预热差：

- indicators 全部 ``min_periods=1``（ma/rsi/bollinger 均是）——窗口内第
  i 根的指标值只有当 i ≥ 精确预热帧数 warmup 时才与全历史重算一致；
- 若"持有至反向"的状态其触发事件落在预热区（idx < warmup）或根本在窗口
  之前，窗口内重算的末端仓位可能与全历史不一致。

最小窗口长度公式（写入各子类 docstring，max_hold_upper 为"预期最长持有
期上界"的契约声明，默认 60 根；持有超过上界属窗口契约外，由 S3 对账
用等长预热窗核实）::

    无状态策略（ma_cross / trend_follow）：
        min_window = warmup + 1
        （+1 为变化检测需要的次末帧：仓位变化判定要用 tail 与 tail-1 两帧
        都精确。ma_cross warmup=slow；trend_follow warmup=n+slope_lookback）
    事件帧策略（rsi_reversal / boll_breakout）：
        min_window = warmup + max_hold_upper
        （rsi_reversal warmup=period+1，RSI 自 diff 起算；
         boll_breakout warmup=period）
        语义：预热窗保证触发事件可被精确重算 + 最长持有上界保证"窗口前
        遗留持有"在契约上不覆盖整个窗口。

窗口不足或状态不可证时 **fail-closed**：返回中性 Verdict（recommendation=
"hold"、confidence=0.0、score=0.0、extras.fail_closed=True + 原因），绝不
给猜出来的信号。残余风险（S3 对账注意事项）：窗口 ≥ min_window 时，若
窗口前遗留持有横贯整个窗口且从未出现反向事件，窗口重算可能把"持多"误判
为"空仓"（hold 语义层 benign，均出 hold），或把遗留持有期内的指标再触发
误判为新开多（false buy）——后者由 max_hold_upper 契约上界压制，S3 对账
脚本须用与回测等长的预热窗（BATCH1_DOSIER rsi/boll 节既列风险点）。

只读包装铁律：不 import、不修改 strategies.py 与 agent_platform 本体；
策略函数与身份基类均懒加载/路径兜底加载（模块导入期不拉起任何执行链）。
"""
import importlib.util
import os
import sys

# ---------------------------------------------------------------- 路径 #
_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                               # 仓库根

# 模板基准分（与 classic_adapter 口径一致；S4 影子期校准位）
BUY_SCORE = 70.0
SELL_SCORE = 30.0
HOLD_SCORE = 0.0
BULL_HOLD_SCORE = 70.0
BUY_SELL_CONFIDENCE = 0.5
HOLD_CONFIDENCE = 0.3
FAIL_CLOSED_CONFIDENCE = 0.0


_CONTRACT_MOD_NAME = "ap_engine_strategy_registry_singleton"
_AP_REGISTRY_MODULE = None


def _load_ap_contract():
    """加载 agent_platform 身份契约（StrategyAgent/Verdict），三段兜底：

    ① engine 目录已在 sys.path（shadow_strategies 同款平 import）；
    ② agent_platform 可作为包导入；
    ③ 按文件路径加载（不污染 sys.path、不触发 agent_platform 包 __init__）。

    无论哪条路径成功，均以 ``_CONTRACT_MOD_NAME`` 单例缓存——测试或其他
    消费方再次按路径加载本模块时拿到同一份类对象，避免 isinstance 断裂。
    """
    global _AP_REGISTRY_MODULE
    if _AP_REGISTRY_MODULE is not None:
        return _AP_REGISTRY_MODULE.StrategyAgent, _AP_REGISTRY_MODULE.Verdict
    mod = sys.modules.get(_CONTRACT_MOD_NAME)
    if mod is not None:
        _AP_REGISTRY_MODULE = mod
        return mod.StrategyAgent, mod.Verdict
    try:
        from strategy_registry import StrategyAgent, Verdict
        mod = sys.modules["strategy_registry"]
    except ImportError:
        try:
            from agent_platform.engine.strategy_registry import StrategyAgent, Verdict
            mod = sys.modules["agent_platform.engine.strategy_registry"]
        except ImportError:
            path = os.path.join(_REPO_ROOT, "agent_platform", "engine",
                                "strategy_registry.py")
            spec = importlib.util.spec_from_file_location(
                _CONTRACT_MOD_NAME, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[_CONTRACT_MOD_NAME] = mod  # 先注册再执行（防环）
            spec.loader.exec_module(mod)
    _AP_REGISTRY_MODULE = mod
    return mod.StrategyAgent, mod.Verdict


def ap_registry_module():
    """返回已加载的 agent_platform/engine/strategy_registry 模块（单例）。"""
    _load_ap_contract()
    return _AP_REGISTRY_MODULE


_STRATEGY_AGENT, _VERDICT = _load_ap_contract()


def _strategy_fn(name):
    """strategies.REGISTRY[name] 只读引用（懒加载，模块导入期不拉起）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    import strategies as _strat
    try:
        return _strat.REGISTRY[name]
    except KeyError as exc:
        raise ValueError("未知经典策略: %s（可选: %s）"
                         % (name, sorted(_strat.REGISTRY))) from exc


def _default_params(name):
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    import strategies as _strat
    return dict(_strat.PARAM_DEFAULTS.get(name, {}))


class ClassicStrategyAgent(_STRATEGY_AGENT):
    """经典 (df, params) -> position 策略 → StrategyAgent 基类。

    子类以**类属性**参数化（strategy_discovery.load_manifest 以无参构造
    ``agent_class()`` 实例化，构造参数不可用，故全部走子类化）：
    - ``strategy_name``：strategies.REGISTRY 键名；
    - ``warmup``：指标精确预热帧数（≥ 该帧指标值与全历史重算一致）；
    - ``event_frame``：True = ffill 事件帧语义（启用窗口契约校验）；
    - ``max_hold_upper``：预期最长持有期上界（仅事件帧策略）；
    - ``min_window``：最小窗口长度（见模块 docstring 公式），子类覆写。

    身份属性按 MANIFEST_GUIDE §4：family="classic"、生命周期影子期为
    "experimental"（转正后升 active）、四策略仅做多 stance="bull"。
    """

    strategy_name = ""
    warmup = 0
    event_frame = False
    max_hold_upper = 60
    min_window = 0

    # 身份属性（MANIFEST_GUIDE §4）
    version = "1.0.0"
    family = "classic"
    capability_manifest = ("daily_bars", "long_only", "frame_position")
    lifecycle_state = "experimental"
    stance = "bull"

    # ------------------------------------------------------------------ #
    # 主入口（StrategyAgent 契约）
    # ------------------------------------------------------------------ #
    def propose(self, context):
        symbol = context.get("symbol", "")
        df = self._build_df(context)
        if df is None or len(df) < self.min_window:
            return self._fail_closed(
                symbol, "窗口不足（fail-closed）：min_window=%d，实际 %s"
                % (self.min_window, 0 if df is None else len(df)))
        pos = _strategy_fn(self.strategy_name)(df, self._params())
        values = [int(v) for v in pos.tolist()]
        cur = values[-1]
        prev = values[-2] if len(values) >= 2 else 0
        if self.event_frame and not self._event_state_verifiable(values):
            return self._fail_closed(
                symbol, "持有状态不可证（fail-closed）：触发事件落在预热区"
                "（warmup=%d）或窗口前，窗口重算与全历史可能不一致"
                % self.warmup)
        return self._to_verdict(symbol, prev, cur, len(values),
                                context.get("trace_id", ""))

    # ------------------------------------------------------------------ #
    # context 解析与窗口纪律
    # ------------------------------------------------------------------ #
    def _build_df(self, context):
        """context bars/closes → 最小 df（strategies 只用 df["close"]）。

        返回 None 表示 context 无可用行情（调用方 fail-closed）。懒 import
        pandas：插件模块导入期零重型依赖。
        """
        bars = context.get("bars")
        if bars:
            import pandas as pd
            df = pd.DataFrame(list(bars))
            if "date" in df.columns:
                df.index = pd.DatetimeIndex(df["date"])
            if "close" not in df.columns:
                return None
            return df
        closes = context.get("closes")
        if closes:
            import pandas as pd
            return pd.DataFrame({"close": [float(c) for c in closes]})
        return None

    def _params(self):
        return dict(_default_params(self.strategy_name))

    def _event_state_verifiable(self, values):
        """事件帧状态可证性：末端若持多，其触发事件必须落在窗口内预热区
        之后（idx >= warmup）。空仓是隐含默认态，无需证明。"""
        if values[-1] != 1:
            return True
        run_start = None
        for i in range(len(values) - 1, 0, -1):
            if values[i] != values[i - 1]:
                run_start = i
                break
        if run_start is None:
            return False  # 全程 1 且无事件：窗口契约不可能，防御性拒绝
        return run_start >= self.warmup

    # ------------------------------------------------------------------ #
    # Verdict 构造（只产建议，绝不写账户/订单/账本）
    # ------------------------------------------------------------------ #
    def _to_verdict(self, symbol, prev, cur, bars_used, trace_id=""):
        if prev == 0 and cur == 1:
            rec, score, conf, stance = "buy", BUY_SCORE, BUY_SELL_CONFIDENCE, "bull"
            note = "新开多（0→1），次日开盘生效"
        elif prev == 1 and cur == 0:
            rec, score, conf, stance = "sell", SELL_SCORE, BUY_SELL_CONFIDENCE, "bear"
            note = "平多（1→0），次日开盘生效"
        elif cur == 1:
            rec, score, conf, stance = "hold", BULL_HOLD_SCORE, HOLD_CONFIDENCE, "bull"
            note = "持多不变，延续多头观点"
        else:
            rec, score, conf, stance = "hold", HOLD_SCORE, HOLD_CONFIDENCE, "neutral"
            note = "空仓不变"
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol, recommendation=rec,
            confidence=conf, stance=stance, score=score,
            rationale="%s：仓位 %d→%d（%s），参数 %s"
                      % (self.strategy_id, prev, cur, note, self._params()),
            trace_id=trace_id,
            extras={
                "prev_position": prev, "position": cur,
                "bars_used": bars_used, "min_window": self.min_window,
                "warmup": self.warmup, "event_frame": self.event_frame,
                "s4_calibrate": "分数/置信为模板基准值，S4 影子期校准",
            },
        )

    def _fail_closed(self, symbol, reason):
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=FAIL_CLOSED_CONFIDENCE,
            stance="neutral", score=0.0, rationale=reason,
            extras={"fail_closed": True, "reason": reason,
                    "min_window": self.min_window},
        )


class MaCrossAgent(ClassicStrategyAgent):
    """ma_cross（均线交叉，无状态逐帧）。

    最小窗口公式：min_window = slow + 1（slow 期均线在 idx ≥ slow-1 才
    与全历史一致；变化检测需 tail 与 tail-1 两帧都精确 → +1）。
    """
    strategy_name = "ma_cross"
    strategy_id = "ma_cross"
    warmup = 20          # PARAM_DEFAULTS.slow；窗口纪律按默认参数声明，
    min_window = 21      # 参数寻优改配置时同步升 version 并改本值


class TrendFollowAgent(ClassicStrategyAgent):
    """trend_follow（价格在 N 日线上且斜率为正，无状态逐帧）。

    最小窗口公式：min_window = n + slope_lookback + 1（slope=i 需
    m[i-slope_lookback] 精确 → i ≥ n-1+slope_lookback；变化检测再 +1）。
    """
    strategy_name = "trend_follow"
    strategy_id = "trend_follow"
    warmup = 25          # n(20) + slope_lookback(5)
    min_window = 26


class RsiReversalAgent(ClassicStrategyAgent):
    """rsi_reversal（RSI 反转，事件帧 ffill 持有至反向事件）。

    最小窗口公式：min_window = warmup + max_hold_upper = (period+1) + 60。
    period+1 为 RSI 精确预热（delta 自第 2 根起算，period 窗才与全历史
    一致）；max_hold_upper 为预期最长持有期上界契约。窗口不足或持有状态
    不可证时 fail-closed（见模块 docstring 残余风险与 S3 对账注意事项）。
    """
    strategy_name = "rsi_reversal"
    strategy_id = "rsi_reversal"
    event_frame = True
    warmup = 15          # period(14) + 1
    min_window = 75      # 15 + max_hold_upper(60)


class BollBreakoutAgent(ClassicStrategyAgent):
    """boll_breakout（布林突破，事件帧 ffill 持有至中轨跌破）。

    最小窗口公式：min_window = warmup + max_hold_upper = period + 60。
    period 期 rolling 均值/标准差在 idx ≥ period-1 才与全历史一致。
    fail-closed 纪律同 RsiReversalAgent。
    """
    strategy_name = "boll_breakout"
    strategy_id = "boll_breakout"
    event_frame = True
    warmup = 20          # period(20)
    min_window = 80      # 20 + max_hold_upper(60)
