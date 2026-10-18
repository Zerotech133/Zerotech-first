# -*- coding: utf-8 -*-
"""缠论 li122 买向过滤器 S2 Agent 化测试（li122-agent 工位）。

覆盖：
① Li122Agent propose 输出 Verdict 结构合法——真调
   agent_platform/engine/strategy_registry.py 的 Verdict.validate() 与
   StrategyRegistry（注册 + run 出口校验），非复制粘贴式校验；
② 驱动模式与 chanlun_li122.judge 直算逐场景一致（ok 布尔 + checks
   明细 + no 拦截原因；S3 对账 100% 硬门槛的测试级预演）；
③ 拦截语义：ok=False → hold/confidence=0/extras.no + extras.checks；
   全场景不产 sell（li122 为买向过滤器，无卖向语义）；
④ fail-closed：中/低级窗口不足、上级预热不足、注入不完整、chanlun
   本体导入失败（config.json 缺失场景注入）→ 中性 Verdict 不出信号；
⑤ 自足模式：现算上级信号（组内最大 dt）与驱动模式同源一致；无买向
   信号 → 合法判定 hold（extras.no="no_upper_signal"）；
⑥ dt 字典序语义：日线上级 dt 当日分钟桶全排（回测基线口径逐字保留，
   不得"聪明地"修正）；
⑦ 无写副作用（context 映射零变更）+ 懒加载隔离（模块导入期零 chanlun
   依赖，config.json 隐藏 IO 不在导入期触发）+ 源码红线静态断言。

数据全部合成（零 DB 依赖）；chanlun 本体只读引用，不修改。
环境缺 chanlun config.json 时相关用例 pytest.skip（fail-visible，
SKIP 原因写明「chanlun config.json 缺失」，绝不静默）。
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))          # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                      # 仓库根
_AGENTS_DIR = os.path.join(_QB_ROOT, "strategy_layer", "onboarding", "agents")

if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

SYMBOL = "TEST.SH"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 被测插件包（先加载：其内部单例化 agent_platform 契约模块）
li122_agent = _load_module(
    "li122_agent_under_test",
    os.path.join(_AGENTS_DIR, "li122_agent.py"))
# agent_platform 契约（真校验器）：复用插件已加载的同一份模块对象，
# 保证 StrategyAgent/Verdict 类身份一致（isinstance 可判定）
_ap_registry = li122_agent.ap_registry_module()

# chanlun 本体直算基线（只读引用）。导入失败（典型：chanlun_data.py:36
# 导入期读 config.json 而该文件缺失）不炸收集，改由 fixture fail-visible 跳过
try:
    from chanlun_li122 import judge as _direct_judge
    from chanlun_li122 import RULESETS as _RULESETS
    from chanlun_signal import analyze as _direct_analyze
    _CHANLUN_ERR = None
except Exception as _exc:  # pragma: no cover - 仅缺 config.json 环境触发
    _CHANLUN_ERR = _exc


@pytest.fixture
def chanlun_ready():
    """chanlun 依赖可用性闸门：缺失时 fail-visible 跳过（原因写明根因）。"""
    if _CHANLUN_ERR is not None:
        pytest.skip("chanlun config.json 缺失（chanlun_data.py 导入期读取 "
                    "quant_backtest/config.json），chanlun 本体链不可用，"
                    "相关用例跳过——导入异常：%r" % (_CHANLUN_ERR,))


AGENT = li122_agent.Li122Agent


class Li122B(AGENT):
    """档 B 变体：类属性参数化子类（无参构造范式）。"""
    ruleset_key = "B_D_30F_5F"


class Li122A1(AGENT):
    """严格变体：上级仅一买参与联立。"""
    ruleset_key = "A1_30F_5F_1F_only1"


class Li122A2(AGENT):
    """敏感性变体：更长观察窗 + 更大容差。"""
    ruleset_key = "A2_30F_5F_1F_wide"


class Li122BadRuleset(AGENT):
    """未知 ruleset 键：应回落默认档而非硬拒。"""
    ruleset_key = "NO_SUCH_RULESET"


# ---------------------------------------------------------------------- #
# 合成数据（dt 全为字符串，口径与回测逐字一致）
# ---------------------------------------------------------------------- #
def _dt_seq(start, n, step_min):
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M")
    return [(t0 + timedelta(minutes=step_min * i)).strftime("%Y-%m-%d %H:%M")
            for i in range(n)]


def _pattern_bars(dts, pattern):
    """按 4 根周期循环的锯齿 h/l 形态（中级底分型可稳定成立）。"""
    return [{"dt": d, "o": 100.0, "h": pattern[i % len(pattern)][0],
             "l": pattern[i % len(pattern)][1], "c": 100.0, "v": 1.0}
            for i, d in enumerate(dts)]


def _v_bars(dts, trough_idx, slope):
    """V 形低点：trough_idx 前下行、后上行（低级 HL 语义可过 check_low）。"""
    out = []
    trough = 100.0 - slope * trough_idx
    for i, d in enumerate(dts):
        low = (100.0 - slope * i) if i <= trough_idx else \
              (trough + slope * (i - trough_idx))
        out.append({"dt": d, "o": 100.0, "h": low + 2.0, "l": low,
                    "c": 100.0, "v": 1.0})
    return out


def _desc_bars(dts, slope):
    """单调下行低点（低级持续创新低 → check_low 不过）。"""
    return [{"dt": d, "o": 100.0, "h": 102.0 - slope * i,
             "l": 100.0 - slope * i, "c": 100.0, "v": 1.0}
            for i, d in enumerate(dts)]


ZIG = [(101.0, 99.0), (100.5, 98.0), (101.0, 99.0), (101.5, 99.5)]


def _bars_a():
    """档 A 数据：1m 400 根（V 形，谷在 idx200）+ 5m 80 根锯齿。"""
    return {"1m": _v_bars(_dt_seq("2026-06-01 09:30", 400, 1), 200, 0.05),
            "5m": _pattern_bars(_dt_seq("2026-06-01 09:30", 80, 5), ZIG)}


def _bars_a_low_fail():
    """档 A 数据但低级持续创新低（check_low_no_lower_low 不过）。"""
    return {"1m": _desc_bars(_dt_seq("2026-06-01 09:30", 400, 1), 0.05),
            "5m": _pattern_bars(_dt_seq("2026-06-01 09:30", 80, 5), ZIG)}


def _bars_b():
    """档 B 数据：30m 60 根锯齿（中级）+ 5m 80 根 V 形（低级）。"""
    return {"30m": _pattern_bars(_dt_seq("2026-06-01 09:30", 60, 30), ZIG),
            "5m": _v_bars(_dt_seq("2026-06-01 09:30", 80, 5), 40, 0.1)}


UPPER_A = {"type": "1买", "dt": "2026-06-01 16:10", "price": 90.0}
UPPER_B = {"type": "1买", "dt": "2026-06-03", "price": 90.0}       # 日线级 dt
UPPER_B_SAMEDAY = {"type": "1买", "dt": "2026-06-01", "price": 90.0}


def _ctx_driver(upper, bars, **extra):
    ctx = {"symbol": SYMBOL, "as_of": upper["dt"],
           "upper_signal": dict(upper), "bars": bars}
    ctx.update(extra)
    return ctx


def _ctx_self_suff(bars, as_of, **extra):
    ctx = {"symbol": SYMBOL, "as_of": as_of, "bars": bars}
    ctx.update(extra)
    return ctx


def _drift_zig(dts, amp=1.0):
    """锯齿叠加漂移：前 100 根下行、后 20 根上行（制造潜在 1买 结构）。"""
    out = []
    for i, d in enumerate(dts):
        base = (120.0 - 0.2 * i) if i < 100 else (100.0 + 0.2 * (i - 100))
        if i % 2 == 0:
            h, l = base + amp, base - amp
        else:
            h, l = base + 0.5 * amp, base - 1.5 * amp
        out.append({"dt": d, "o": base, "h": h, "l": l, "c": base, "v": 1.0})
    return out


def _bars_self_suff():
    """自足模式数据：30m 120 根（上级现算）+ 档 A 中/低级。"""
    bars = _bars_a()
    bars["30m"] = _drift_zig(_dt_seq("2026-05-20 09:30", 120, 30))
    return bars


SELF_SUFF_AS_OF = _dt_seq("2026-05-20 09:30", 120, 30)[-1]


# 驱动模式对账场景：(名称, context, ruleset, agent 类)
DRIVER_SCENARIOS = [
    ("A_pass", _ctx_driver(UPPER_A, _bars_a()), "A_30F_5F_1F", AGENT),
    ("A2_pass", _ctx_driver(UPPER_A, _bars_a()), "A2_30F_5F_1F_wide", Li122A2),
    ("A1_intercept_type",
     _ctx_driver({**UPPER_A, "type": "2买"}, _bars_a()),
     "A1_30F_5F_1F_only1", Li122A1),
    ("A_low_fail", _ctx_driver(UPPER_A, _bars_a_low_fail()),
     "A_30F_5F_1F", AGENT),
    ("B_pass", _ctx_driver(UPPER_B, _bars_b()), "B_D_30F_5F", Li122B),
    ("B_same_day_exclusion",
     _ctx_driver(UPPER_B_SAMEDAY, _bars_b()), "B_D_30F_5F", Li122B),
]


# ---------------------------------------------------------------------- #
# ① Verdict 结构合法（真调 agent_platform 校验器）+ 身份属性
# ---------------------------------------------------------------------- #
def test_propose_returns_valid_verdict(chanlun_ready):
    agent = AGENT()
    v = agent.propose(_ctx_driver(UPPER_A, _bars_a()))
    assert isinstance(v, _ap_registry.Verdict)
    assert v.strategy_id == "chanlun_li122"
    assert v.symbol == SYMBOL
    v.validate()  # 五档枚举 / confidence [0,1] / stance / symbol 白名单
    d = json.loads(json.dumps({
        "recommendation": v.recommendation, "confidence": v.confidence,
        "stance": v.stance, "score": v.score, "extras": v.extras}))
    assert d["recommendation"] in _ap_registry.RECOMMENDATIONS


def test_registry_run_outlet_validation(chanlun_ready):
    """注册 + StrategyRegistry.run 出口校验（真实消费端路径）。"""
    reg = _ap_registry.StrategyRegistry()
    reg.register(AGENT())
    assert reg.list_ids() == ["chanlun_li122"]
    v = reg.run("chanlun_li122", _ctx_driver(UPPER_A, _bars_a()))
    assert isinstance(v, _ap_registry.Verdict) and v.strategy_id == "chanlun_li122"
    m = reg.metadata("chanlun_li122")
    assert m["family"] == "chanlun" and m["lifecycle_state"] == "experimental"


def test_identity_attrs_per_manifest_guide():
    agent = AGENT()
    assert agent.strategy_id == "chanlun_li122"
    assert agent.version and agent.family == "chanlun"
    assert agent.stance in _ap_registry.STANCES
    assert agent.lifecycle_state in _ap_registry.LIFECYCLE_STATES
    assert all(isinstance(c, str) and c for c in agent.capability_manifest)
    # 类属性参数化 + 无参构造（strategy_discovery 兼容）
    assert agent.ruleset_key == "A_30F_5F_1F"
    assert agent.upper_source == "computed"
    assert isinstance(agent.min_upper_bars, int) and agent.min_upper_bars > 0
    for cls in (Li122B, Li122A1, Li122BadRuleset):
        sub = cls()
        assert sub.ruleset_key == cls.ruleset_key


# ---------------------------------------------------------------------- #
# ② 驱动模式与 judge 直算逐场景一致（S3 对账 100% 硬门槛预演）
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("name,ctx,key,cls", DRIVER_SCENARIOS,
                         ids=[s[0] for s in DRIVER_SCENARIOS])
def test_driver_mode_parity_with_direct_judge(name, ctx, key, cls,
                                              chanlun_ready):
    rule = _RULESETS[key]
    direct = _direct_judge(ctx["upper_signal"], ctx["bars"][rule["mid_period"]],
                           ctx["bars"][rule["low_period"]], key)
    v = cls().propose(ctx)
    v.validate()
    # ok 布尔一致率 100%（硬门槛，无公差）：buy ⇔ ok；fail-closed ⇒ 拦截
    assert (v.recommendation == "buy") is bool(direct["ok"]), name
    assert v.recommendation in ("buy", "hold"), name
    if v.extras.get("fail_closed"):
        # Agent 最小窗口闸门先于本体拦截（两者同为 hold/conf=0 一致语义）
        assert direct["ok"] is False and v.confidence == 0.0, name
        return
    assert v.extras["no"] == direct["no"], name
    assert [c["ok"] for c in v.extras["checks"]] == \
           [c["ok"] for c in direct["checks"]], name
    assert [c["detail"] for c in v.extras["checks"]] == \
           [c["detail"] for c in direct["checks"]], name
    assert v.extras["ruleset"] == key


def test_pass_verdict_shape_and_confidence(chanlun_ready):
    """ok=True → buy + 档位置信 + bull + 全量诊断 extras。"""
    cases = [(AGENT, _ctx_driver(UPPER_A, _bars_a()), 0.7),
             (Li122A1, _ctx_driver(UPPER_A, _bars_a()), 0.75),
             (Li122B, _ctx_driver(UPPER_B, _bars_b()), 0.65)]
    for cls, ctx, conf in cases:
        v = cls().propose(ctx)
        v.validate()
        assert v.recommendation == "buy" and v.stance == "bull"
        assert v.confidence == conf and v.score == 70.0
        assert v.extras["no"] is None
        assert len(v.extras["checks"]) == 3
        assert all(c["ok"] for c in v.extras["checks"])
        assert "联立放行" in v.rationale


# ---------------------------------------------------------------------- #
# ③ 拦截语义：hold/conf=0/extras.no + 全场景不产 sell
# ---------------------------------------------------------------------- #
def test_intercepted_verdict_shape(chanlun_ready):
    v = Li122A1().propose(_ctx_driver({**UPPER_A, "type": "2买"}, _bars_a()))
    v.validate()
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.stance == "neutral" and v.score == 0.0
    assert v.extras["no"] == "upper_type"
    assert v.extras["checks"][0]["ok"] is False
    assert "联立拦截" in v.rationale and "不产 sell" in v.rationale
    assert not v.extras.get("fail_closed")  # 合法拦截，非 fail-closed


def test_never_sell_across_scenarios(chanlun_ready):
    """li122 无卖向语义：任何场景 recommendation ∈ {buy, hold}。"""
    contexts = [ctx for _, ctx, _, _ in DRIVER_SCENARIOS]
    contexts.append(_ctx_self_suff(_bars_self_suff(), SELF_SUFF_AS_OF))
    for cls in (AGENT, Li122B, Li122A1, Li122BadRuleset):
        for ctx in contexts:
            v = cls().propose(ctx)
            assert v.recommendation in ("buy", "hold"), (cls, ctx.get("as_of"))


def test_ruleset_fallback_not_hard_reject(chanlun_ready):
    """未知 ruleset 键 → 回落默认档 A 并标注（不硬拒、不 raise）。"""
    v = Li122BadRuleset().propose(_ctx_driver(UPPER_A, _bars_a()))
    v.validate()
    assert v.recommendation == "buy" and v.extras["ruleset"] == "A_30F_5F_1F"
    assert v.extras["requested_ruleset"] == "NO_SUCH_RULESET"
    assert "回落默认档" in v.rationale
    assert "回落默认档" in v.extras["ruleset_fallback"]


# ---------------------------------------------------------------------- #
# ④ fail-closed：窗口不足 / 预热不足 / 注入不完整 / 本体导入失败
# ---------------------------------------------------------------------- #
def _assert_fail_closed(v, reason_kw):
    v.validate()
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.stance == "neutral" and v.score == 0.0
    assert v.extras.get("fail_closed") is True
    assert reason_kw in v.rationale


def test_fail_closed_mid_window_insufficient():
    bars = _bars_a()
    bars["5m"] = bars["5m"][:2]  # < mid_confirm_bars+1=3
    _assert_fail_closed(AGENT().propose(_ctx_driver(UPPER_A, bars)),
                        "中级窗口不足")


def test_fail_closed_low_window_insufficient():
    bars = _bars_a()
    bars["1m"] = bars["1m"][:100]  # < 2×low_window=120
    _assert_fail_closed(AGENT().propose(_ctx_driver(UPPER_A, bars)),
                        "低级窗口不足")


def test_fail_closed_missing_period_bars():
    bars = _bars_a()
    del bars["1m"]
    _assert_fail_closed(AGENT().propose(_ctx_driver(UPPER_A, bars)),
                        "低级窗口不足")


def test_fail_closed_upper_injection_incomplete():
    ctx = {"symbol": SYMBOL, "as_of": "2026-06-01 16:10",
           "upper_signal": {"type": "1买"}, "bars": _bars_a()}  # 缺 dt
    _assert_fail_closed(AGENT().propose(ctx), "注入不完整")


def test_fail_closed_self_suff_warmup_insufficient():
    bars = _bars_self_suff()
    bars["30m"] = bars["30m"][:50]  # < min_upper_bars=100
    _assert_fail_closed(
        AGENT().propose(_ctx_self_suff(bars, bars["30m"][-1]["dt"])),
        "预热不足")


def test_fail_closed_when_chanlun_unavailable(monkeypatch):
    """config.json 缺失（本体导入失败）→ fail-closed 出中性，绝不 raise。

    用 sys.modules 注入 None 模拟 `from chanlun_li122 import ...` 失败
    （等价于 chanlun_data.py:36 导入期读 config.json 抛错）。
    """
    monkeypatch.setitem(sys.modules, "chanlun_li122", None)
    v = AGENT().propose(_ctx_driver(UPPER_A, _bars_a()))
    _assert_fail_closed(v, "config.json")


# ---------------------------------------------------------------------- #
# ⑤ 自足模式：现算上级信号与驱动模式同源一致
# ---------------------------------------------------------------------- #
def test_self_suff_parity_with_driver(chanlun_ready):
    bars = _bars_self_suff()
    agent = AGENT()
    v = agent.propose(_ctx_self_suff(bars, SELF_SUFF_AS_OF))
    v.validate()
    # 测试内直算期望：同源 analyze + BUY 过滤 + 组内最大 dt（与 agent 同规则）
    cut30 = [b for b in bars["30m"] if b["dt"] <= SELF_SUFF_AS_OF]
    st = _direct_analyze(cut30)
    cands = [s for s in st["signals"]
             if s["type"] in _RULESETS["A_30F_5F_1F"]["upper_types"]]
    assert v.extras["upper_source"] == "computed"
    if not cands:
        assert v.recommendation == "hold" and v.confidence == 0.0
        assert v.extras["no"] == "no_upper_signal"
        return
    expected = max(cands, key=lambda s: s["dt"])
    assert v.extras["upper_dt"] == expected["dt"]
    # 与驱动模式（注入同一条信号）逐字段同源一致
    v2 = agent.propose(_ctx_driver(expected, bars))
    assert v.recommendation == v2.recommendation
    assert round(v.confidence, 6) == round(v2.confidence, 6)
    assert v.extras["no"] == v2.extras["no"]
    assert [c["detail"] for c in v.extras["checks"]] == \
           [c["detail"] for c in v2.extras["checks"]]


def test_self_suff_no_buy_signal_is_legal_hold(chanlun_ready):
    """自足模式无买向信号 → 合法判定 hold（非 fail-closed）。"""
    dts30 = _dt_seq("2026-05-20 09:30", 150, 30)
    flat = [{"dt": d, "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1.0}
            for d in dts30]  # 全平序列：无结构 → 无信号
    bars = _bars_a()
    bars["30m"] = flat
    v = AGENT().propose(_ctx_self_suff(bars, dts30[-1]))
    v.validate()
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.extras["no"] == "no_upper_signal"
    assert not v.extras.get("fail_closed")
    assert v.extras["checks"] == []


# ---------------------------------------------------------------------- #
# ⑥ dt 字典序语义：日线上级 dt 当日分钟桶全排（基线口径逐字保留）
# ---------------------------------------------------------------------- #
def test_daily_upper_dt_excludes_same_day_bars(chanlun_ready):
    """日线 dt="2026-06-01"：当日 30m/5m 桶全被排除（截至前一日收盘）。"""
    ctx = _ctx_driver(UPPER_B_SAMEDAY, _bars_b())
    rule = _RULESETS["B_D_30F_5F"]
    # 本体直算佐证：同数据同口径下 judge 的中级截断为 0 根
    direct = _direct_judge(ctx["upper_signal"], ctx["bars"]["30m"],
                           ctx["bars"]["5m"], "B_D_30F_5F")
    assert direct["ok"] is False
    assert direct["checks"][1]["detail"].startswith("mid_bars=0")
    # Agent：窗口不足 fail-closed（不"聪明地"修正基线口径）
    v = Li122B().propose(ctx)
    _assert_fail_closed(v, "中级窗口不足")


# ---------------------------------------------------------------------- #
# ⑦ 无写副作用 + 懒加载隔离 + 源码红线
# ---------------------------------------------------------------------- #
class _NoWriteMapping(dict):
    """记录一切写操作的 context 映射。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append(key)
        raise AssertionError("Agent 写 context: %r" % (key,))

    def __delitem__(self, key):
        self.writes.append(key)
        raise AssertionError("Agent 删 context: %r" % (key,))

    def setdefault(self, key, default=None):
        self.writes.append(key)
        raise AssertionError("Agent setdefault: %r" % (key,))

    def pop(self, key, *default):
        self.writes.append(key)
        raise AssertionError("Agent pop: %r" % (key,))

    def update(self, *a, **kw):
        self.writes.append("update")
        raise AssertionError("Agent update context")

    def clear(self):
        self.writes.append("clear")
        raise AssertionError("Agent clear context")


@pytest.mark.parametrize("mode", ["driver", "self_sufficient"])
def test_propose_has_no_write_side_effects(mode, chanlun_ready):
    agent = AGENT()
    ctx = _NoWriteMapping(_ctx_driver(UPPER_A, _bars_a()) if mode == "driver"
                          else _ctx_self_suff(_bars_self_suff(),
                                              SELF_SUFF_AS_OF))
    v = agent.propose(ctx)
    v.validate()
    assert ctx.writes == []
    # Agent 实例不持有订单/账本/执行客户端
    for attr in vars(agent).values():
        for forbidden in ("order", "account", "ledger", "exec", "client"):
            assert forbidden not in str(type(attr)).lower()


def test_module_source_free_of_execution_chain():
    """模块源码不引用执行链/账本/下单（红线静态断言）。"""
    src = open(os.path.join(_AGENTS_DIR, "li122_agent.py"),
               encoding="utf-8").read()
    for forbidden in ("execution_engine", "place_order", "cancel_order",
                      "order_book", "ledger_write", "write_ledger",
                      "account", "broker", "trade_api"):
        assert forbidden not in src, "源码含禁用标识: %s" % forbidden


def test_module_import_does_not_pull_chanlun_chain():
    """插件模块导入期零 chanlun 依赖（config.json 隐藏 IO 隔离在 propose）。"""
    before = set(sys.modules)
    fresh = _load_module(
        "li122_agent_fresh_import_check",
        os.path.join(_AGENTS_DIR, "li122_agent.py"))
    assert hasattr(fresh, "Li122Agent")
    new = set(sys.modules) - before
    pulled = [k for k in new if k.startswith("chanlun")]
    assert not pulled, "模块导入期拉起了 chanlun 链: %s" % pulled


def test_bars_input_not_mutated(chanlun_ready):
    """本体纯函数 + 包装层切片拷贝：输入 bar 序列零原地修改。"""
    bars = _bars_a()
    snapshot = json.dumps(bars, sort_keys=True, ensure_ascii=False)
    AGENT().propose(_ctx_driver(UPPER_A, bars))
    assert json.dumps(bars, sort_keys=True, ensure_ascii=False) == snapshot
