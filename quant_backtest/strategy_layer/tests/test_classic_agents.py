# -*- coding: utf-8 -*-
"""经典四策略 S2 Agent 化测试（RC-1 第一批 S2，s2-classic 工位）。

覆盖：
① 四 Agent propose 在合成 context 上输出 Verdict 结构合法——真调
   agent_platform/engine/strategy_registry.py 的 Verdict.validate() 与
   StrategyRegistry（注册 + run 出口校验），非复制粘贴式校验；
② ma_cross 仓位变化帧与 strategies.ma_cross 直算逐帧一致（合成数据
   as-of 逐帧切片，含变化帧 buy/sell 与持有帧 hold）；
③ rsi/boll 事件帧：窗口不足 fail-closed 出中性 Verdict 不出信号；持有
   状态触发事件落在预热区（run-start < warmup）同样 fail-closed；
④ Agent 全程无写副作用断言（context 映射零变更 + 模块源码无执行链
   引用 + Agent 实例无订单/账本属性）。

数据全部合成（零 DB 依赖）；strategies.py 只读引用，不修改。
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))          # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                      # 仓库根
_AGENTS_DIR = os.path.join(_QB_ROOT, "strategy_layer", "onboarding", "agents")

if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

SYMBOL = "TEST.SH"
CLASSIC_IDS = ["ma_cross", "rsi_reversal", "boll_breakout", "trend_follow"]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 被测插件包（先加载：其内部单例化 agent_platform 契约模块）
classic_agents = _load_module(
    "classic_agents_under_test",
    os.path.join(_AGENTS_DIR, "classic_agents.py"))
# agent_platform 契约（真校验器）：复用插件已加载的同一份模块对象，
# 保证 StrategyAgent/Verdict 类身份一致（isinstance 可判定）
_ap_registry = classic_agents.ap_registry_module()
# 策略本体（只读引用，作直算基线）
import strategies as _strategies  # noqa: E402


AGENT_CLASSES = [
    classic_agents.MaCrossAgent,
    classic_agents.RsiReversalAgent,
    classic_agents.BollBreakoutAgent,
    classic_agents.TrendFollowAgent,
]


# ---------------------------------------------------------------------- #
# 合成数据
# ---------------------------------------------------------------------- #
def _synth_closes(n=120):
    """正弦震荡：均线交叉/RSI 极值/布林突破/趋势斜率翻转全覆盖。"""
    return [100.0 + 30.0 * np.sin(i / 5.0) for i in range(n)]


@pytest.fixture(scope="module")
def synth_closes():
    return _synth_closes()


def _ctx(closes, symbol=SYMBOL, **extra):
    ctx = {"symbol": symbol, "closes": list(closes)}
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------- #
# ① Verdict 结构合法（真调 agent_platform 校验器）
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.strategy_id)
def test_propose_returns_valid_verdict(cls, synth_closes):
    agent = cls()
    v = agent.propose(_ctx(synth_closes))
    assert isinstance(v, _ap_registry.Verdict)
    assert v.strategy_id == cls.strategy_id
    assert v.symbol == SYMBOL
    v.validate()  # 五档枚举 / confidence [0,1] / stance / symbol 白名单
    d = json.loads(json.dumps({
        "recommendation": v.recommendation, "confidence": v.confidence,
        "stance": v.stance, "score": v.score, "extras": v.extras}))
    assert d["recommendation"] in _ap_registry.RECOMMENDATIONS


def test_registry_run_outlet_validation(synth_closes):
    """注册 + StrategyRegistry.run 出口校验（真实消费端路径）。"""
    reg = _ap_registry.StrategyRegistry()
    for cls in AGENT_CLASSES:
        reg.register(cls())
    assert sorted(reg.list_ids()) == sorted(CLASSIC_IDS)
    for sid in reg.list_ids():
        v = reg.run(sid, _ctx(synth_closes))
        assert isinstance(v, _ap_registry.Verdict) and v.strategy_id == sid
    m = reg.metadata("ma_cross")
    assert m["family"] == "classic" and m["lifecycle_state"] == "experimental"


@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.strategy_id)
def test_identity_attrs_per_manifest_guide(cls):
    agent = cls()
    assert agent.strategy_id == agent.strategy_name == cls.strategy_id
    assert agent.version and agent.family == "classic"
    assert agent.stance in _ap_registry.STANCES
    assert agent.lifecycle_state in _ap_registry.LIFECYCLE_STATES
    assert all(isinstance(c, str) and c for c in agent.capability_manifest)


# ---------------------------------------------------------------------- #
# ② ma_cross 逐帧一致（含仓位变化帧映射）
# ---------------------------------------------------------------------- #
def test_ma_cross_frame_by_frame_matches_direct_compute():
    n = 120
    closes = _synth_closes(n)
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    df = pd.DataFrame({"close": closes}, index=idx)
    pos = _strategies.ma_cross(df, {})
    agent = classic_agents.MaCrossAgent()
    changes = 0
    for i in range(2, n + 1):
        v = agent.propose(_ctx(closes[:i]))
        if i < agent.min_window:
            assert v.extras.get("fail_closed")
            continue
        cur, prev = int(pos.iloc[i - 1]), int(pos.iloc[i - 2])
        expected = ("buy" if (prev == 0 and cur == 1)
                    else "sell" if (prev == 1 and cur == 0) else "hold")
        assert v.recommendation == expected, "frame %d: %s != %s" % (
            i, v.recommendation, expected)
        changes += int(expected in ("buy", "sell"))
        if expected == "buy":
            assert v.score == 70.0 and v.stance == "bull"
        elif expected == "sell":
            assert v.score == 30.0 and v.stance == "bear"
    assert changes >= 2, "合成数据应覆盖开仓与平仓变化帧"


def test_trend_follow_frame_by_frame_matches_direct_compute():
    n = 120
    closes = _synth_closes(n)
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    df = pd.DataFrame({"close": closes}, index=idx)
    pos = _strategies.trend_follow(df, {})
    agent = classic_agents.TrendFollowAgent()
    checked = 0
    for i in range(agent.min_window, n + 1):
        v = agent.propose(_ctx(closes[:i]))
        cur, prev = int(pos.iloc[i - 1]), int(pos.iloc[i - 2])
        expected = ("buy" if (prev == 0 and cur == 1)
                    else "sell" if (prev == 1 and cur == 0) else "hold")
        assert v.recommendation == expected, "frame %d" % i
        checked += 1
    assert checked > 0


# ---------------------------------------------------------------------- #
# ③ 事件帧 fail-closed（rsi_reversal / boll_breakout）
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", [classic_agents.RsiReversalAgent,
                                 classic_agents.BollBreakoutAgent],
                         ids=lambda c: c.strategy_id)
def test_event_frame_insufficient_window_fail_closed(cls, synth_closes):
    agent = cls()
    for cut in (0, 5, agent.min_window - 1):
        v = agent.propose(_ctx(synth_closes[:cut] if cut else []))
        v.validate()
        assert v.recommendation == "hold"
        assert v.confidence == 0.0 and v.score == 0.0
        assert v.stance == "neutral"
        assert v.extras.get("fail_closed") and "窗口不足" in v.rationale
        assert "buy" != v.recommendation and "sell" != v.recommendation


def test_event_frame_window_sufficient_matches_direct():
    """窗口 ≥ min_window 时事件帧重算与直算一致（触发/持有/平仓）。"""
    n = 120
    closes = _synth_closes(n)
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    df = pd.DataFrame({"close": closes}, index=idx)
    for name, cls in (("rsi_reversal", classic_agents.RsiReversalAgent),
                      ("boll_breakout", classic_agents.BollBreakoutAgent)):
        pos = _strategies.REGISTRY[name](df, {})
        agent = cls()
        for i in range(agent.min_window, n + 1):
            v = agent.propose(_ctx(closes[:i]))
            assert not v.extras.get("fail_closed"), "frame %d" % i
            cur, prev = int(pos.iloc[i - 1]), int(pos.iloc[i - 2])
            expected = ("buy" if (prev == 0 and cur == 1)
                        else "sell" if (prev == 1 and cur == 0) else "hold")
            assert v.recommendation == expected, "%s frame %d" % (name, i)


def test_rsi_hold_from_warmup_zone_fail_closed():
    """持有状态触发事件落在预热区（run-start < warmup）→ fail-closed。

    构造：前 10 根单调下跌（RSI 部分预热期即触发 <oversold），此后长期
    横盘（不再出现反向事件）——末端持多但触发帧不可精确重算，必须拒绝
    出信号而非给出 hold-bull。
    """
    agent = classic_agents.RsiReversalAgent()
    assert agent.min_window == agent.warmup + agent.max_hold_upper
    n = agent.min_window + 10
    closes = [100.0 - 2.0 * i for i in range(10)]            # 触发区
    closes += [closes[-1] + 0.01 * i for i in range(n - 10)]  # 长期横盘
    v = agent.propose(_ctx(closes))
    v.validate()
    assert v.extras.get("fail_closed")
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert "不可证" in v.rationale


def test_boll_window_sufficient_emits_events():
    """布林：长期横盘后尾根跳空上破上轨 → 末端 buy（窗口充足可证）。"""
    agent = classic_agents.BollBreakoutAgent()
    flat = [100.0] * (agent.min_window + 5)
    closes = flat + [120.0]  # 尾根突破
    v = agent.propose(_ctx(closes))
    v.validate()
    assert not v.extras.get("fail_closed")
    assert v.recommendation == "buy" and v.stance == "bull"
    assert v.score == 70.0 and v.confidence == 0.5


# ---------------------------------------------------------------------- #
# ④ 无写副作用
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


@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.strategy_id)
def test_propose_has_no_write_side_effects(cls, synth_closes):
    agent = cls()
    ctx = _NoWriteMapping(_ctx(synth_closes))
    v = agent.propose(ctx)
    v.validate()
    assert ctx.writes == []
    # Agent 实例不持有订单/账本/执行客户端
    for attr in vars(agent).values():
        for forbidden in ("order", "account", "ledger", "exec", "client"):
            assert forbidden not in str(type(attr)).lower()


def test_module_source_free_of_execution_chain():
    """模块源码不引用执行链/账本/下单（红线静态断言）。"""
    src = open(os.path.join(_AGENTS_DIR, "classic_agents.py"),
               encoding="utf-8").read()
    for forbidden in ("execution_engine", "place_order", "cancel_order",
                      "order_book", "ledger_write", "write_ledger",
                      "account", "broker", "trade_api"):
        assert forbidden not in src, "源码含禁用标识: %s" % forbidden
    init_src = open(os.path.join(_AGENTS_DIR, "__init__.py"),
                    encoding="utf-8").read()
    assert "execution" not in init_src


def test_import_does_not_pull_execution_chain():
    """插件模块导入后，执行链模块未被拉起（import 副作用断言）。"""
    fresh = _load_module(
        "classic_agents_fresh_import_check",
        os.path.join(_AGENTS_DIR, "classic_agents.py"))
    assert hasattr(fresh, "ClassicStrategyAgent")
    for forbidden in ("execution_engine", "order", "account"):
        assert forbidden not in sys.modules or not sys.modules[
            forbidden].__name__.startswith("quant_backtest")
