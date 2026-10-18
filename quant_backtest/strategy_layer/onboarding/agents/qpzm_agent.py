# -*- coding: utf-8 -*-
"""青萍之末（qpzm）S2 Agent 化（QpzmAgent）——首个截面强依赖 Agent。

本体：quant_backtest/qpzm_backtest.py::combine_score（jev_mode="missing"
单格语义，纯函数逐字复刻——本模块**不 import** qpzm_backtest / 
qpzm_daily_batch / rps_resonance 任何本体，仅以类属性快照常量 + 纯函数
复刻单格公式，导入期绝对干净，连 pandas/numpy 都不依赖）。

定案依据：C:/aios/automation_runs/qpzm_s2_recon_20261018.md（8 项定案）。

单票逐日 propose（定案 1）：combine_score 的每个 (date, code) 格只依赖
5 个装配层预注入标量（tech / rps_mix / env_score / env_safe / eligible）
+ 常量 W4 与阈值。截面依赖（rank / 广播）全部发生在 rps_mix 与 env 生成
之前，归装配层（K254 后复权因子链、RPS 截面、环境闸门均在装配层按本体
原函数预计算——复用即同源）。Agent 是纯单格公式：5 标量 → Verdict，
不接触任何价格。

context 契约（装配层注入、Agent 只读）::

    {"symbol": "000993.SZ",           # wide 列名口径（全码形态）
     "as_of": 20261016,               # int64 YYYYMMDD，与 wide 索引同形
     "fields": {
        "tech": float|None,           # tech_score 单格（None ≡ NaN 缺维）
        "rps_mix": float|None,        # cross_rps 单格（None ≡ NaN 缺维）
        "n_avail": int,               # RPS 可用周期数 1/2/3（extras 用）
        "env_score": float,           # env_gate["score"] 当日（日频广播）
        "env_safe": bool,             # env_gate["safe"] 当日
        "eligible": bool},            # n_bars >= 55
     "trace_id": str（可选）}

  fields 缺键 / 类型坏 / env_safe 或 eligible 缺失 → fail-closed（定案 4
  行 7：「不可证」绝不给猜出来的信号）。as_of 是否为 wide 索引日归装配层
  校验（Agent 零 wide 索引知识，校验即依赖）。None ≡ NaN 缺维（JSON 口径
  的 NaN 表示），参与单格公式而非 fail-closed。

单格公式（逐字复刻 combine_score jev_mode="missing"，运算次序保持同序，
对拍容差 1e-9——侦察 R1 风险对冲）::

    el  = eligible ∧ tech 非 NaN          # 本体: (eligible & tech.notna())
    hr  = el ∧ rps_mix 非 NaN             # 本体: el & ~isnan(rp)
    wsum = (0.35 + 0.15)·el + 0.25·hr     # jev 缺维 renorm
    num  = tech·0.35 + env_score·0.15 + (0.0 if rps NaN else rps_mix·0.25)
    sc   = num / wsum   (wsum > 0)；wsum == 0 → NaN
    ge   = -1e9 if sc NaN else sc         # 本体 np.nan_to_num 口径
    buy  = el ∧ hr ∧ gate_ok ∧ ge >= buy_th
    obs  = el ∧ hr ∧ gate_ok ∧ obs_th <= ge < buy_th

Verdict 映射（定案 4 五档表，信息保全策略；行 3/4/5/6 是**合法 hold** 非
fail-closed——本体在这些条件下确定性不产生信号，可复现判定）：

| # | 本体状态                                   | recommendation | confidence | stance  | score      | extras 关键键 |
|---|--------------------------------------------|----------------|------------|---------|------------|----------------|
| 1 | 买入：el ∧ hr ∧ gate_ok ∧ sc≥75            | buy            | 0.7        | bull    | sc 原值    | action="买入", n_avail, s4_calibrate |
| 2 | 观察：el ∧ hr ∧ gate_ok ∧ 50≤sc<75         | buy            | 0.4        | bull    | sc 原值    | action="观察", tier="observe" |
| 3 | 评分不足：el ∧ hr ∧ gate_ok ∧ sc<50        | hold           | 0.0        | neutral | sc 原值    | no="score_below" |
| 4 | 环境闸门否决：el ∧ hr ∧ ¬gate_safe          | hold           | 0.0        | neutral | sc 原值    | action="回避（环境闸门）", veto="env_gate" |
| 5 | RPS 缺维：el ∧ ¬hr                          | hold           | 0.0        | neutral | sc 伪分原值| no="rps_missing" |
| 6 | 不入池：¬eligible（n_bars<55 或 tech NaN）  | hold           | 0.0        | neutral | 0.0        | no="min_bars" |
| 7 | 装配不完整（fields 缺键/类型坏）            | hold           | 0.0        | neutral | 0.0        | fail_closed=True + reason |
| 8 | 任何异常兜底（propose 永不 raise）          | hold           | 0.0        | neutral | 0.0        | fail_closed=True + %r 异常 |

- 观察 → buy/0.4 而非 hold：批次回测（K238）把买入+观察整批当候选——
  观察是「弱买入立场」不是中性；extras.action 保留中文原档，回放可完全
  反解三档：action∈{买入,观察} ⇔ recommendation="buy"。
- 行 5 hr 压死语义：rps 缺维票 renorm 伪分（wsum=0.5）可过阈（错杀清单
  rps_missing 组判例），但永不信号——score 承载伪分原值、判定 hold。
- 不产 sell/strong_sell：qpzm 纯多头截面筛选，无卖向语义。
- score 承载 combine_score 浮点原值（0~100），**不做 K233 取整**。

已知 live/replay 差异（冻结登记，Agent 不复刻——Agent 决策链 = 回测口径，
S3 对账硬门槛 100% 只对回测口径达成；R3 风险：不得当 bug 修）：
- K233：产线 rps_resonance.py:724 先 score=int(round(score)) 再进阈值，
  边界票（≈75/50）live/replay 可翻档；Agent 保留浮点原值，聚合层如需
  live 对齐自行后处理；
- S5 次新降级：产线 rps_resonance.py:738 CN 次新买入→「观察（次新）」，
  回测无此降级；Agent 不做（extras.n_avail 提供次新判据供聚合层使用）；
- JEV 四维口径：Agent 唯一口径 = jev_mode="missing" 三维降权 renorm
  （与 S3 主基线、backfill replay 行同源）；live 四维口径不做。

W3-4 分位豁免（定案 2）：分位是推送卡片展示增强（annotate_percentile），
不参与 action 判定——Agent 决策链零感知，不读 rps_daily_batches.jsonl、
不消费分位基准、Verdict 决策链无 percentile。若 S4 推送需要分位，由聚合
层在 extras 后处理注入，不进 propose。

参数化（无参构造，strategy_discovery 兼容）：全部参数走类属性，变体
子类化——12 变体全量网格锚中，w_eq/w_alt 只改变装配层 cross_rps 的 RPS
权重（Agent 收到的 rps_mix 已是成品标量，无需子类化行为；如需注册身份
可建空子类）；th7050 改 buy_th=70.0；gate_off 改 gate_on=False。

红线：零 LLM 调用、零网络、零写副作用（context 零变更、无订单/账本
引用）、不修改 qpzm_backtest.py / qpzm_daily_batch.py / rps_resonance.py
等任何本体文件与 li122/classic agents（并行线产物）；Agent 是纯包装层。
"""
import importlib.util
import math
import os
import sys

# ---------------------------------------------------------------- 路径 #
_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                               # 仓库根

# ── 单格公式常量（qpzm_backtest.py 只读抄值快照，不 import） ──
# W4 共振权重（jev_mode="missing" 下 jev 维整体缺席，其余三维 renorm）
W_TECH = 0.35    # W4["tech"]
W_RPS = 0.25     # W4["rps"]
W_ENV = 0.15     # W4["env"]
BUY_TH = 75.0    # TH_CUR 买入阈
OBS_TH = 50.0    # TH_CUR 观察阈
# 分数不可证（NaN）时信号判定用的 nan_to_num 下探值（本体 combine_score 同款）
_NAN_GE = -1e9

# 置信档位（S2 定案 4；S4 影子期用实际前向收益校准后再调）
CONF_BUY = 0.7
CONF_OBS = 0.4


_CONTRACT_MOD_NAME = "ap_engine_strategy_registry_singleton"
_AP_REGISTRY_MODULE = None


def _load_ap_contract():
    """加载 agent_platform 身份契约（StrategyAgent/Verdict），三段兜底：

    ① engine 目录已在 sys.path（shadow_strategies 同款平 import）；
    ② agent_platform 可作为包导入；
    ③ 按文件路径加载（不污染 sys.path、不触发 agent_platform 包 __init__）。

    无论哪条路径成功，均以 ``_CONTRACT_MOD_NAME`` 单例缓存——与
    li122/classic agents 共享同一份类对象，避免 isinstance 断裂。
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


def _is_num(v):
    """数值判定（bool 是 int 子类，须显式排除——JSON true/false 不是分）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class QpzmAgent(_STRATEGY_AGENT):
    """青萍之末共振打分 Agent（单格纯函数，截面字段装配层预注入）。

    类属性参数化（无参构造，strategy_discovery 兼容）：
    - ``buy_th`` / ``obs_th``：买入/观察阈值（TH_CUR 快照 75/50）；
    - ``gate_on``：环境闸门开关（True=env safe=False 一票否决买入/观察，
      env 分仍计入打分——与本体 gate_on 语义一致）。

    身份属性按 MANIFEST_GUIDE §4：family="cross_section"（首个截面强依赖
    Agent，capability_manifest 声明 cross_section_context——RPS 是全市场
    rank，单票历史序列推不出 RPS，无自足模式，context 契约完整性由装配层
    保证，缺字段走 fail-closed 而非现算兜底）、影子期为 "experimental"、
    纯多头 stance="bull"。
    """

    strategy_id = "qpzm"
    version = "1.0.0"
    family = "cross_section"
    capability_manifest = ("cross_section_context", "long_only")
    lifecycle_state = "experimental"
    stance = "bull"

    buy_th = BUY_TH
    obs_th = OBS_TH
    gate_on = True

    # ------------------------------------------------------------------ #
    # 主入口（StrategyAgent 契约，绝不 raise）
    # ------------------------------------------------------------------ #
    def propose(self, context):
        symbol = ""
        try:
            symbol = context.get("symbol", "") if context else ""
            return self._propose(context, symbol)
        except Exception as exc:  # 兜底降级：对齐 li122 兜底语义
            return self._fail_closed(
                symbol, "propose 异常降级（绝不 raise）：%r" % (exc,))

    def _propose(self, context, symbol):
        fields = context.get("fields")
        if not isinstance(fields, dict):
            return self._fail_closed(
                symbol, "fields 缺失或非映射（装配不完整，fail-closed）")
        parsed = self._parse_fields(fields)
        if isinstance(parsed, str):
            return self._fail_closed(symbol, parsed)
        (tech, rps_mix, n_avail, env_score, env_safe, eligible) = parsed
        as_of = context.get("as_of", "")
        trace_id = context.get("trace_id", "")

        # ── 单格公式（逐字复刻 combine_score jev_mode="missing"，同运算次序） ──
        t = float("nan") if tech is None else float(tech)
        rp = float("nan") if rps_mix is None else float(rps_mix)
        el = bool(eligible) and not math.isnan(t)   # 本体: eligible & tech.notna()
        hr = el and not math.isnan(rp)              # 本体: el & ~isnan(rp)
        wsum = (W_TECH + W_ENV) * (1.0 if el else 0.0) \
            + W_RPS * (1.0 if hr else 0.0)
        num = t * W_TECH + env_score * W_ENV \
            + (0.0 if math.isnan(rp) else rp * W_RPS)
        sc = num / wsum if wsum > 0 else float("nan")
        gate_ok = env_safe if self.gate_on else True
        ge = _NAN_GE if math.isnan(sc) else sc      # 本体 np.nan_to_num 口径

        base_extras = {"as_of": as_of, "n_avail": n_avail,
                       "tech": None if math.isnan(t) else t,
                       "rps_mix": None if math.isnan(rp) else rp,
                       "env_score": env_score, "env_safe": env_safe,
                       "eligible": eligible, "gate_on": self.gate_on}

        # ── Verdict 映射（定案 4 五档表，行序即优先级） ──
        if el and hr and gate_ok and ge >= self.buy_th:      # 行 1 买入
            extras = dict(base_extras, action="买入", s4_calibrate=
                          "置信为模板基准值（score 为 combine_score 浮点原值），"
                          "S4 影子期校准置信")
            return self._signal_verdict(symbol, "buy", CONF_BUY, sc, trace_id,
                                        "共振买入：combine_score=%.6f ≥ 买入阈"
                                        " %s（闸门放行）" % (sc, self.buy_th),
                                        extras)
        if el and hr and gate_ok and ge >= self.obs_th:      # 行 2 观察（弱买入）
            extras = dict(base_extras, action="观察", tier="observe")
            return self._signal_verdict(symbol, "buy", CONF_OBS, sc, trace_id,
                                        "共振观察：%.6f ∈ [观察阈 %s, 买入阈 %s)"
                                        "（弱买入立场，闸门放行）"
                                        % (sc, self.obs_th, self.buy_th),
                                        extras)
        if not el:                                            # 行 6 不入池
            why = ("tech 缺维（NaN）强制 el=False" if bool(eligible)
                   else "n_bars < 55 不入池")
            return _VERDICT(
                strategy_id=self.strategy_id, symbol=symbol,
                recommendation="hold", confidence=0.0, stance="neutral",
                score=0.0,
                rationale="不入池（%s）：合法判定，非 fail-closed" % why,
                trace_id=trace_id,
                extras=dict(base_extras, no="min_bars"))
        if not hr:                                            # 行 5 RPS 缺维
            if math.isnan(sc):
                rationale = ("RPS 缺维且 env 缺分：伪分不可证，不信号"
                             "（合法判定）")
            else:
                rationale = ("RPS 缺维：renorm 伪分 %.6f 过阈也不信号"
                             "（hr 压死语义，合法判定）" % sc)
            return _VERDICT(
                strategy_id=self.strategy_id, symbol=symbol,
                recommendation="hold", confidence=0.0, stance="neutral",
                score=0.0 if math.isnan(sc) else sc,
                rationale=rationale,
                trace_id=trace_id,
                extras=dict(base_extras, no="rps_missing"))
        if not gate_ok:                                       # 行 4 环境闸门否决
            return _VERDICT(
                strategy_id=self.strategy_id, symbol=symbol,
                recommendation="hold", confidence=0.0, stance="neutral",
                score=0.0 if math.isnan(sc) else sc,
                rationale="环境闸门否决：env safe=False 一票否决买入/观察"
                          "（本体确定性否决，合法判定）",
                trace_id=trace_id,
                extras=dict(base_extras, action="回避（环境闸门）",
                            veto="env_gate"))
        # 行 3 评分不足（el ∧ hr ∧ gate_ok ∧ sc<obs_th）
        if math.isnan(sc):
            rationale = ("combine_score 不可证（NaN）：按本体 nan_to_num "
                         "口径远低于阈值，不信号（合法判定）")
        else:
            rationale = ("评分不足：combine_score=%.6f < 观察阈 %s"
                         "（合法判定）" % (sc, self.obs_th))
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=0.0, stance="neutral",
            score=0.0 if math.isnan(sc) else sc,
            rationale=rationale,
            trace_id=trace_id,
            extras=dict(base_extras, no="score_below"))

    # ------------------------------------------------------------------ #
    # fields 解析（缺键/类型坏 → fail-closed 原因文本；None ≡ NaN 缺维）
    # ------------------------------------------------------------------ #
    def _parse_fields(self, fields):
        """→ (tech, rps_mix, n_avail, env_score, env_safe, eligible) 或
        fail-closed 原因文本。类型契约见模块 docstring。"""
        out = []
        for key, checker in (
                ("tech", lambda v: v is None or _is_num(v)),
                ("rps_mix", lambda v: v is None or _is_num(v)),
                ("n_avail", lambda v: isinstance(v, int)
                    and not isinstance(v, bool)),
                ("env_score", _is_num),
                ("env_safe", lambda v: isinstance(v, bool)),
                ("eligible", lambda v: isinstance(v, bool))):
            if key not in fields:
                return ("fields 缺键 %r（装配不完整，fail-closed）" % (key,))
            v = fields[key]
            if not checker(v):
                return ("fields[%r] 类型坏：%r（装配不完整，fail-closed）"
                        % (key, v))
            out.append(v)
        return tuple(out)

    # ------------------------------------------------------------------ #
    # Verdict 构造（只产建议，零写副作用）
    # ------------------------------------------------------------------ #
    def _signal_verdict(self, symbol, recommendation, confidence, sc,
                        trace_id, rationale, extras):
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation=recommendation, confidence=confidence,
            stance="bull", score=0.0 if math.isnan(sc) else sc,
            rationale=rationale, trace_id=trace_id, extras=extras)

    def _fail_closed(self, symbol, reason):
        return _VERDICT(
            strategy_id=self.strategy_id, symbol=symbol,
            recommendation="hold", confidence=0.0, stance="neutral",
            score=0.0, rationale=reason,
            extras={"fail_closed": True, "reason": reason,
                    "buy_th": self.buy_th, "obs_th": self.obs_th,
                    "gate_on": self.gate_on})


class QpzmThAltAgent(QpzmAgent):
    """阈值变体：TH_ALT 70/50（网格锚 th7050）。"""
    buy_th = 70.0
    obs_th = 50.0


class QpzmGateOffAgent(QpzmAgent):
    """闸门关闭变体：env safe=False 不否决（env 分仍计入打分，gate_off）。"""
    gate_on = False
