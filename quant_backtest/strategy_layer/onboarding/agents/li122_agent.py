# -*- coding: utf-8 -*-
"""缠论 li122 买向过滤器 S2 Agent 化（Li122Agent）。

本体：quant_backtest/chanlun_li122.py::judge（纯函数，一行不改）——
三级别联立买向过滤：上级买向信号 + 中级底分型成立 + 低级不新低，
判定/拦截二值输出。li122 无卖向语义（五档中的 sell 留给其他策略），
联立不改变信号生成逻辑，只做放行/拦截。

对齐消费端（agent_platform，只读契约底座）：
- 基类：``agent_platform/engine/strategy_registry.py::StrategyAgent``——
  子类实现 ``propose(context) -> Verdict``，五档 + confidence [0,1] +
  stance，经 ``Verdict.validate()`` 白名单校验；
- 无参构造（strategy_discovery.load_manifest 以 ``agent_class()`` 实例化），
  全部参数走类属性；变体档位用子类化（如 ruleset_key="B_D_30F_5F"）。

context 契约（S2 扩型：首个多级别上下文消费者，装配层注入、Agent 只读）::

    {"symbol": str,
     "as_of": "YYYY-MM-DD HH:MM" | "YYYY-MM-DD",    # 驱动判定的时点
     "upper_signal": {"type","dt","price"} | None,   # 注入 → 驱动模式
     "bars": {"30m"/"5m"/"1m"/"1d": [升序 bar dict...]},
     "trace_id": str（可选）}

  bar dt 为**字符串**，格式与回测逐字一致（分钟桶 "YYYY-MM-DD HH:MM"
  会话分桶起点标签、日线 "YYYY-MM-DD"），禁止 tz-aware ISO 漂移——
  格式漂移即字典序语义漂移。

双运行模式（同一 propose 入口）：
- 驱动模式（S3 对账口径）：context 带 upper_signal → 直接 judge，
  逐信号可复现；
- 自足模式（S4 影子口径）：只带 bars + as_of → 内部先对上级周期 bars
  按 dt <= as_of 字典序截断，再 chanlun_signal.analyze 现算（回测基线
  同源路径，不经信号池），过滤 upper_types 买向信号后取 dt 最大一条
  （chan_monitor「组内最大 dt」判例；消费游标归调用方，Agent 无状态）。

Verdict 映射（过滤语义 → 五档，信息保全策略）：
- judge ok=True  → recommendation="buy"，confidence 按 ruleset 档位
  （A1_30F_5F_1F_only1=0.75 / A_30F_5F_1F=0.7 / B_D_30F_5F=0.65 /
  A2_30F_5F_1F_wide=0.6，三查全过的中高置信），stance="bull"；
- judge ok=False → recommendation="hold"，confidence=0.0，stance=
  "neutral"，extras.no=拦截原因（首个未过检查名）+ extras.checks
  全量诊断明细；不产 sell；
- 窗口不足 / 预热区不可证 / 注入不完整 / 本体导入失败 → fail-closed
  中性 Verdict（confidence=0.0，extras.fail_closed=True + 原因），
  绝不给猜出来的信号；
- 自足模式无买向信号 → 合法判定 hold（extras.no="no_upper_signal"，
  非 fail-closed）；
- ruleset 键未知 → 回落默认档 A_30F_5F_1F 并在 rationale/extras 标注
  （不硬拒，对齐影子主链 fail-open 纪律）。

导入期隔离（S2 侦察 1.3 定案）：chanlun_li122 → chanlun_fractal →
chanlun_data，而 chanlun_data.py:36 在模块导入期读 config.json（隐藏
IO）。本模块 import 期零 chanlun 依赖，judge / chanlun_signal 全部
懒加载进 propose；环境缺 config.json 时 fail-closed 出中性 Verdict
（原因注明 chanlun config.json 缺失），绝不 raise。

dt 字典序切片语义（逐字保留回测口径，禁止"聪明地"修正）：
- 中/低级序列按 b["dt"] <= 信号 dt 字符串比较截断（桶 dt 为起点标签，
  恰在信号桶内的进行中桶被排除 → 天然前视阻断）；
- 日线上级 dt="YYYY-MM-DD" 时当日全部分钟桶被排除（实际评估截至
  前一日收盘）——回测基线既有口径，复现不得修正；
- 驱动模式下 Agent 先按信号 dt 截断再交 judge（与本体内部 _cut 同
  语义、幂等），保证越界数据零泄漏。

最小窗口口径（fail-closed 阈值，参照 classic_agents 公式与 manifest）：
- 中级：rule.mid_confirm_bars + 1（与 judge 中级数据不足闸门同源）；
- 低级：2 × rule.low_window（不新低双窗）；
- 上级（自足模式现算）：min_upper_bars=100（对齐回测 collect 的
  len(bars30) >= 100 结构预热）；
- 上级信号缺失（驱动模式注入为空/缺 type/dt）→ fail-closed。

红线：零 LLM 调用、零网络、零写副作用（context 零变更、无订单/账本
引用）、不修改 chanlun_li122.py / chanlun_fractal.py / chanlun_data.py
等任何本体文件；Agent 是纯包装层。
"""
import importlib.util
import os
import sys

# ---------------------------------------------------------------- 路径 #
_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                               # 仓库根

# 模板基准分（与 classic_adapter / classic_agents 口径一致；S4 校准位）
BUY_SCORE = 70.0

# judge ok=True 的 confidence 按 ruleset 档位（S2 侦察 3.3 定案：
# 最严档最高置信；S4 影子期用实际前向收益校准后再调）
OK_CONFIDENCE = {
    "A_30F_5F_1F": 0.7,
    "A1_30F_5F_1F_only1": 0.75,
    "A2_30F_5F_1F_wide": 0.6,
    "B_D_30F_5F": 0.65,
}
OK_CONFIDENCE_DEFAULT = 0.6

DEFAULT_RULESET = "A_30F_5F_1F"
NO_UPPER_SIGNAL = "no_upper_signal"


_CONTRACT_MOD_NAME = "ap_engine_strategy_registry_singleton"
_AP_REGISTRY_MODULE = None


def _load_ap_contract():
    """加载 agent_platform 身份契约（StrategyAgent/Verdict），三段兜底：

    ① engine 目录已在 sys.path（shadow_strategies 同款平 import）；
    ② agent_platform 可作为包导入；
    ③ 按文件路径加载（不污染 sys.path、不触发 agent_platform 包 __init__）。

    无论哪条路径成功，均以 ``_CONTRACT_MOD_NAME`` 单例缓存——与
    classic_agents 共享同一份类对象，避免 isinstance 断裂。
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


def _chanlun_judge():
    """chanlun_li122.judge 只读引用（懒加载：导入链含 chanlun_data 的
    导入期 config.json 读取，必须隔离到 propose 运行期）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    from chanlun_li122 import judge, RULESETS
    return judge, RULESETS


def _chanlun_signal_analyze():
    """chanlun_signal.analyze 只读引用（懒加载，自足模式现算上级信号）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    from chanlun_signal import analyze
    return analyze


def _cut_bars(bars, dt):
    """升序 bars 字典序截断至 dt（与 chanlun_li122._cut 同语义：
    列表推导生成新列表，零原地修改；字符串比较，逐字保留基线口径）。"""
    return [b for b in bars if b.get("dt", "") <= dt]


class Li122Agent(_STRATEGY_AGENT):
    """缠论 li122 买向过滤器 Agent（专用类，非 Classic 通用件）。

    类属性参数化（无参构造，strategy_discovery 兼容）：
    - ``ruleset_key``：chanlun_li122.RULESETS 键，默认 A_30F_5F_1F；
    - ``upper_source``：声明上级信号默认口径（"computed" 现算；实际
      运行模式由 context 是否注入 upper_signal 决定，注入优先）；
    - ``min_upper_bars``：自足模式上级结构预热下限。

    身份属性按 MANIFEST_GUIDE §4：family="chanlun"、影子期为
    "experimental"（达标准 promotions 流程晋升）、买向过滤 stance="bull"。
    """

    strategy_id = "chanlun_li122"
    version = "1.0.0"
    family = "chanlun"
    capability_manifest = ("multi_timeframe_bars", "chanlun_structure_filter",
                           "long_only")
    lifecycle_state = "experimental"
    stance = "bull"

    ruleset_key = DEFAULT_RULESET
    upper_source = "computed"
    min_upper_bars = 100

    # ------------------------------------------------------------------ #
    # 主入口（StrategyAgent 契约，绝不 raise）
    # ------------------------------------------------------------------ #
    def propose(self, context):
        symbol = context.get("symbol", "")
        try:
            return self._propose(context, symbol)
        except Exception as exc:  # 兜底降级：对齐 chan_monitor 降级语义
            return self._fail_closed(
                symbol, "联立判定异常降级（绝不 raise）：%r" % (exc,))

    def _propose(self, context, symbol):
        as_of = context.get("as_of", "")
        trace_id = context.get("trace_id", "")
        # ① 懒加载本体（导入期 config.json 环境耦合隔离在此）
        try:
            judge, rulesets = _chanlun_judge()
        except Exception as exc:
            return self._fail_closed(
                symbol, "chanlun 依赖导入失败（常见原因：chanlun "
                        "config.json 缺失，chanlun_data 导入期读取该文件）"
                        "：%r" % (exc,))
        # ② ruleset 解析（未知键回落默认档，不硬拒）
        requested = self.ruleset_key
        ruleset_key = requested if requested in rulesets else DEFAULT_RULESET
        fallback_note = None
        if ruleset_key != requested:
            fallback_note = ("ruleset_key=%r 不在 RULESETS，回落默认档 %s"
                             % (requested, ruleset_key))
        rule = rulesets[ruleset_key]
        # ③ 上级信号解析（注入优先，否则自足现算）
        upper, mode = self._resolve_upper(context, rule)
        if mode == "fail_closed":
            return self._fail_closed(symbol, upper)
        if mode == "no_signal":
            return self._hold_no_signal(symbol, as_of, ruleset_key, trace_id)
        sig_dt = upper["dt"]
        # ④ 中/低级序列按信号 dt 字典序截断（幂等于本体内部 _cut）
        bars = context.get("bars") or {}
        mid = _cut_bars(bars.get(rule["mid_period"]) or [], sig_dt)
        low = _cut_bars(bars.get(rule["low_period"]) or [], sig_dt)
        # ⑤ 最小窗口闸门（窗口不足/预热区不可证 → fail-closed）
        if len(mid) < rule["mid_confirm_bars"] + 1:
            return self._fail_closed(
                symbol, "中级窗口不足（fail-closed）：信号 dt=%r 截断后 "
                        "mid=%d 根 < mid_confirm_bars+1=%d"
                        % (sig_dt, len(mid), rule["mid_confirm_bars"] + 1))
        if len(low) < 2 * rule["low_window"]:
            return self._fail_closed(
                symbol, "低级窗口不足（fail-closed）：信号 dt=%r 截断后 "
                        "low=%d 根 < 2×low_window=%d"
                        % (sig_dt, len(low), 2 * rule["low_window"]))
        # ⑥ 本体判定（纯函数，零改动直接调用）
        res = judge(upper, mid, low, ruleset_key)
        extras = {"ruleset": ruleset_key, "checks": res["checks"],
                  "no": res["no"], "upper_source": mode,
                  "upper_type": upper.get("type"), "upper_dt": sig_dt,
                  "as_of": as_of, "requested_ruleset": requested}
        if fallback_note:
            extras["ruleset_fallback"] = fallback_note
        # ⑦ Verdict 映射（过滤语义，不产 sell）
        if res["ok"]:
            conf = OK_CONFIDENCE.get(ruleset_key, OK_CONFIDENCE_DEFAULT)
            rationale = ("联立放行：%s@%s 三查全过（%s）"
                         % (upper.get("type"), sig_dt, ruleset_key))
            if fallback_note:
                rationale += "；" + fallback_note
            return _VERDICT(
                strategy_id=self.strategy_id, symbol=symbol,
                recommendation="buy", confidence=conf, stance="bull",
                score=BUY_SCORE, rationale=rationale, trace_id=trace_id,
                extras=extras)
        failed = next((c for c in res["checks"] if not c["ok"]), None)
        rationale = ("联立拦截（%s）：%s；li122 为买向过滤器，不产 sell"
                     % (res["no"], failed["detail"] if failed else "未知"))
        if fallback_note:
            rationale += "；" + fallback_note
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=0.0, stance="neutral",
            score=0.0, rationale=rationale, trace_id=trace_id,
            extras=extras)

    # ------------------------------------------------------------------ #
    # 上级信号解析（注入优先，否则自足现算 + 组内最大 dt 选最新）
    # ------------------------------------------------------------------ #
    def _resolve_upper(self, context, rule):
        """→ (upper_signal, mode)；mode ∈ driver/computed/no_signal/
        fail_closed（fail_closed 时首元素为原因文本）。"""
        upper = context.get("upper_signal")
        if isinstance(upper, dict) and upper:
            if not upper.get("type") or not upper.get("dt"):
                return ("上级信号注入不完整（缺 type/dt），判定不可证"
                        "（fail-closed）", "fail_closed")
            return upper, "driver"
        as_of = context.get("as_of", "")
        bars_up = (context.get("bars") or {}).get(rule["upper_period"]) or []
        cut = _cut_bars(bars_up, as_of)
        if len(cut) < self.min_upper_bars:
            return ("上级结构预热不足（自足模式，fail-closed）：%s 截断至 "
                    "as_of=%r 仅 %d 根 < min_upper_bars=%d"
                    % (rule["upper_period"], as_of, len(cut),
                       self.min_upper_bars), "fail_closed")
        try:
            st = _chanlun_signal_analyze()(cut)
        except Exception as exc:
            return ("chanlun_signal 现算失败（常见原因：chanlun "
                    "config.json 缺失）：%r" % (exc,), "fail_closed")
        cands = [s for s in (st.get("signals") or [])
                 if s.get("type") in rule["upper_types"]
                 and str(s.get("dt", "")) <= as_of]
        if not cands:
            return None, "no_signal"
        # 组内最大 dt 判例：每标的每 as_of 只取最新一条（QA-20261017），
        # per-day 调度节拍下选信号口径冻结；Agent 无状态，消费游标归调用方
        return max(cands, key=lambda s: s["dt"]), "computed"

    # ------------------------------------------------------------------ #
    # Verdict 构造（只产建议，零写副作用）
    # ------------------------------------------------------------------ #
    def _hold_no_signal(self, symbol, as_of, ruleset_key, trace_id=""):
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=0.0, stance="neutral",
            score=0.0,
            rationale="自足模式：as_of=%r 截断序列无 upper_types 买向信号"
                      "（合法判定，非 fail-closed）" % (as_of,),
            trace_id=trace_id,
            extras={"ruleset": ruleset_key, "checks": [],
                    "no": NO_UPPER_SIGNAL, "upper_source": "computed",
                    "as_of": as_of})

    def _fail_closed(self, symbol, reason):
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=0.0, stance="neutral",
            score=0.0, rationale=reason,
            extras={"fail_closed": True, "reason": reason,
                    "ruleset": self.ruleset_key,
                    "min_upper_bars": self.min_upper_bars})
