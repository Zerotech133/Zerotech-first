# -*- coding: utf-8 -*-
"""青萍之末 qpzm S2 Agent 化测试（qpzm-s2 工位）。

覆盖：
① QpzmAgent propose 输出 Verdict 结构合法——真调
   agent_platform/engine/strategy_registry.py 的 Verdict.validate() 与
   StrategyRegistry（注册 + run 出口校验），非复制粘贴式校验；
② 1e-9 逐值对拍（侦察 R1 风险对冲硬门槛）：合成标量网格 ≥50 组合 ×
   3 个 Agent 变体（现行 75/50 闸门开 / th7050 / gate_off）逐格对照
   qpzm_backtest.combine_score 直算结果——判定布尔 100% 一致 + score
   浮点 1e-9 容差一致。qpzm_backtest 仅**测试侧** import 读公式，
   Agent 模块本身零 qpzm 依赖；
③ 五档映射逐行：买入 buy/0.7/bull、观察 buy/0.4/bull（弱买入非中性）、
   评分不足/闸门否决/rps_missing/min_bars 合法 hold（非 fail-closed）、
   装配不完整/异常 fail-closed hold；全场景不产 sell；
④ hr 压死语义：RPS 缺维票 renorm 伪分过阈（score=100）也永不信号；
⑤ fail-closed：fields 缺键（逐键）/类型坏（逐键）/非映射/异常兜底
   ——propose 永不 raise；
⑥ 零写副作用（context 映射零变更）+ fresh import 零 qpzm 依赖断言
   （不拉起 qpzm_backtest / qpzm_daily_batch / rps_resonance，连
   pandas/numpy 都不依赖）+ 源码红线静态断言。

数据全部合成（零 DB / 零 parquet 依赖）；qpzm_backtest 只读引用，
不修改任何本体文件。
"""
import importlib.util
import json
import math
import os
import random
import sys

import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))          # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                      # 仓库根
_AGENTS_DIR = os.path.join(_QB_ROOT, "strategy_layer", "onboarding", "agents")

if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

SYMBOL = "000993.SZ"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 被测插件包（先加载：其内部单例化 agent_platform 契约模块）
qpzm_agent = _load_module(
    "qpzm_agent_under_test",
    os.path.join(_AGENTS_DIR, "qpzm_agent.py"))
# agent_platform 契约（真校验器）：复用插件已加载的同一份模块对象，
# 保证 StrategyAgent/Verdict 类身份一致（isinstance 可判定）
_ap_registry = qpzm_agent.ap_registry_module()

# combine_score 直算基线（只读引用，仅测试侧消费公式；模块导入期零 IO）
import qpzm_backtest as _qpzm_bt  # noqa: E402

AGENT = qpzm_agent.QpzmAgent


class QpzmThAlt(AGENT):
    """阈值变体：TH_ALT 70/50。"""
    buy_th = 70.0


class QpzmGateOff(AGENT):
    """闸门关闭变体。"""
    gate_on = False


# ---------------------------------------------------------------------- #
# 合成 context 与 combine_score 直算基线
# ---------------------------------------------------------------------- #
def _ctx(tech=None, rps_mix=None, n_avail=3, env_score=50.0, env_safe=True,
         eligible=True, **extra):
    ctx = {"symbol": SYMBOL, "as_of": 20261016,
           "fields": {"tech": tech, "rps_mix": rps_mix, "n_avail": n_avail,
                      "env_score": env_score, "env_safe": env_safe,
                      "eligible": eligible}}
    ctx.update(extra)
    return ctx


def _direct_combine(tech, rps_mix, env_score, env_safe, eligible,
                    buy_th=75.0, obs_th=50.0, gate_on=True):
    """qpzm_backtest.combine_score 单格直算（测试专用 import 读公式）。

    返回 (sc, buy, obs)——与 Agent 逐格对拍的期望值来源。
    """
    t = float("nan") if tech is None else float(tech)
    r = float("nan") if rps_mix is None else float(rps_mix)
    df_t = pd.DataFrame([[t]], dtype=float)
    df_r = pd.DataFrame([[r]], dtype=float)
    df_e = pd.DataFrame([[bool(eligible)]])
    env_df = pd.DataFrame({"score": [float(env_score)],
                           "safe": [bool(env_safe)]})
    sc, buy, obs = _qpzm_bt.combine_score(
        df_t, df_r, env_df, buy_th=float(buy_th), obs_th=float(obs_th),
        gate_on=gate_on, eligible=df_e, jev_mode="missing")
    return float(sc.iloc[0, 0]), bool(buy.iloc[0, 0]), bool(obs.iloc[0, 0])


def _expected_hold_label(tech, rps_mix, env_safe, eligible, gate_on):
    """hold 行标签期望（与 Agent 行序同构的互斥划分，全部合法判定）。"""
    el = bool(eligible) and tech is not None
    if not el:
        return "min_bars", None
    if rps_mix is None:
        return "rps_missing", None
    if gate_on and not env_safe:
        return None, "env_gate"
    return "score_below", None


# ---------------------------------------------------------------------- #
# 对拍网格：边界手工判例 + 随机网格（≥50 组合硬门槛）
# ---------------------------------------------------------------------- #
def _edge_combos():
    """手工边界判例：(tech, rps_mix, env_score, env_safe, eligible)。"""
    return [
        # sc 恰落 75.0（二进制精确：56.25/0.75）→ 买入边界（ge>=buy_th 含）
        (80.0, 83.0, 50.0, True, True),
        # sc 恰落 50.0 → 观察边界（obs_th 下闭）
        (50.0, 56.0, 40.0, True, True),
        # sc 恰低于 75 / 恰低于 50（逐值贴边）
        (80.0, 82.999999, 50.0, True, True),
        (50.0, 55.999999, 40.0, True, True),
        # hr 压死判例：RPS 缺维伪分 = (35+15)/0.5 = 100 过阈也不信号
        (100.0, None, 100.0, True, True),
        (80.0, None, 50.0, True, True),
        # tech 缺维 → el=False（min_bars 池外语义）
        (None, 80.0, 50.0, True, True),
        (None, None, 50.0, True, True),
        # 闸门否决（sc 仍承载原值）
        (80.0, 83.0, 50.0, False, True),
        # 行序互斥判例：rps 缺维 × 闸门否决同格 → rps_missing（非 veto）
        (80.0, None, 50.0, False, True),
        # 不入池 × 高分
        (80.0, 83.0, 50.0, True, False),
        (80.0, 83.0, 50.0, False, False),
        # 零分格
        (0.0, 0.0, 0.0, True, True),
        # env_score=0（闸门安全但分拖累）
        (100.0, 100.0, 0.0, True, True),
        # 满分格
        (100.0, 100.0, 100.0, True, True),
        # tech=0 边界
        (0.0, 100.0, 100.0, True, True),
        # rps_mix=0 边界
        (100.0, 0.0, 100.0, True, True),
        # env_score=100
        (60.0, 60.0, 100.0, True, True),
        # 闸门关变体敏感格：safe=False 但 gate_off 不否决
        (90.0, 90.0, 80.0, False, True),
        # 负值防御（装配层异常值也按公式算，绝不 raise）
        (-10.0, -5.0, 30.0, True, True),
    ]


def _random_combos(n=60, seed=20261018):
    """随机标量网格（含 None 缺维概率），确定性种子可复现。"""
    rng = random.Random(seed)
    combos = []
    for _ in range(n):
        tech = None if rng.random() < 0.12 else round(rng.uniform(0, 100), 4)
        rps = None if rng.random() < 0.15 else round(rng.uniform(0, 100), 4)
        env = round(rng.uniform(0, 100), 4)
        safe = rng.random() < 0.6
        elig = rng.random() < 0.8
        combos.append((tech, rps, env, safe, elig))
    return combos


COMBOS = _edge_combos() + _random_combos()
assert len(COMBOS) >= 50, "对拍网格硬门槛：≥50 组合"

VARIANTS = [
    ("base_7550_gate_on", AGENT, dict(buy_th=75.0, obs_th=50.0, gate_on=True)),
    ("th7050", QpzmThAlt, dict(buy_th=70.0, obs_th=50.0, gate_on=True)),
    ("gate_off", QpzmGateOff, dict(buy_th=75.0, obs_th=50.0, gate_on=False)),
]


# ---------------------------------------------------------------------- #
# ① Verdict 结构合法（真调 agent_platform 校验器）+ 身份属性
# ---------------------------------------------------------------------- #
def test_propose_returns_valid_verdict():
    v = AGENT().propose(_ctx(tech=80.0, rps_mix=83.0))
    assert isinstance(v, _ap_registry.Verdict)
    assert v.strategy_id == "qpzm"
    assert v.symbol == SYMBOL
    v.validate()  # 五档枚举 / confidence [0,1] / stance / symbol 白名单
    d = json.loads(json.dumps({
        "recommendation": v.recommendation, "confidence": v.confidence,
        "stance": v.stance, "score": v.score, "extras": v.extras}))
    assert d["recommendation"] in _ap_registry.RECOMMENDATIONS


def test_registry_run_outlet_validation():
    reg = _ap_registry.StrategyRegistry()
    reg.register(AGENT())
    assert reg.list_ids() == ["qpzm"]
    v = reg.run("qpzm", _ctx(tech=80.0, rps_mix=83.0))
    assert isinstance(v, _ap_registry.Verdict) and v.strategy_id == "qpzm"
    m = reg.metadata("qpzm")
    assert m["family"] == "cross_section"
    assert m["lifecycle_state"] == "experimental"
    # 首个截面强依赖 Agent：capability_manifest 声明 cross_section_context
    assert "cross_section_context" in m["capability_manifest"]


def test_identity_attrs_and_class_param():
    agent = AGENT()
    assert agent.strategy_id == "qpzm"
    assert agent.version and agent.family == "cross_section"
    assert agent.stance in _ap_registry.STANCES
    assert agent.lifecycle_state in _ap_registry.LIFECYCLE_STATES
    assert all(isinstance(c, str) and c for c in agent.capability_manifest)
    # 类属性参数化 + 无参构造（strategy_discovery 兼容）+ 变体子类
    assert agent.buy_th == 75.0 and agent.obs_th == 50.0
    assert agent.gate_on is True
    for cls, (bth, oth, gate) in (
            (QpzmThAlt, (70.0, 50.0, True)),
            (QpzmGateOff, (75.0, 50.0, False))):
        sub = cls()
        assert sub.buy_th == bth and sub.obs_th == oth and sub.gate_on is gate


# ---------------------------------------------------------------------- #
# ② 1e-9 逐值对拍（≥50 组合 × 3 变体，判定布尔 100% + score 1e-9）
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("vname,cls,kw", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_parity_with_combine_score_1e9(vname, cls, kw):
    agent = cls()
    n_buy = n_obs = n_hold = 0
    for combo in COMBOS:
        tech, rps_mix, env_score, env_safe, eligible = combo
        sc_direct, buy_direct, obs_direct = _direct_combine(*combo, **kw)
        ctx = _ctx(tech=tech, rps_mix=rps_mix, env_score=env_score,
                   env_safe=env_safe, eligible=eligible, n_avail=3)
        v = agent.propose(ctx)
        v.validate()
        # 判定布尔 100% 硬门槛（无公差）：buy ⇔ buy_m；buy ⇔ obs_m 独立
        if buy_direct:
            assert v.recommendation == "buy" and v.confidence == 0.7, combo
            assert v.extras["action"] == "买入", combo
            n_buy += 1
        elif obs_direct:
            assert v.recommendation == "buy" and v.confidence == 0.4, combo
            assert v.extras["action"] == "观察", combo
            n_obs += 1
        else:
            assert v.recommendation == "hold" and v.confidence == 0.0, combo
            label, veto = _expected_hold_label(tech, rps_mix, env_safe,
                                               eligible, kw["gate_on"])
            assert v.extras.get("no") == label, combo
            assert v.extras.get("veto") == veto, combo
            assert not v.extras.get("fail_closed"), combo
            n_hold += 1
        # score 浮点 1e-9 容差逐格一致（wsum=0 → sc=NaN → Agent 0.0）
        expected = 0.0 if math.isnan(sc_direct) else sc_direct
        assert abs(v.score - expected) <= 1e-9, combo
    # 网格自检：三档都有覆盖（防网格退化成单档）
    assert n_buy > 0 and n_obs > 0 and n_hold > 0


def test_parity_covers_at_least_50_combos():
    assert len(COMBOS) >= 50


# ---------------------------------------------------------------------- #
# ③ 五档映射逐行（含 extras 契约键）
# ---------------------------------------------------------------------- #
def test_row1_buy_shape():
    v = AGENT().propose(_ctx(tech=80.0, rps_mix=83.0, env_score=50.0,
                             n_avail=3))
    v.validate()
    assert v.recommendation == "buy" and v.confidence == 0.7
    assert v.stance == "bull"
    sc, _, _ = _direct_combine(80.0, 83.0, 50.0, True, True)
    assert v.score == pytest.approx(sc, abs=1e-9)
    assert v.extras["action"] == "买入"
    assert v.extras["n_avail"] == 3
    assert "s4_calibrate" in v.extras
    assert not v.extras.get("fail_closed")


def test_row2_observe_is_weak_buy():
    """观察 → buy/0.4/bull（弱买入非中性）；extras 保留中文原档 + tier。"""
    v = AGENT().propose(_ctx(tech=50.0, rps_mix=56.0, env_score=40.0))
    v.validate()
    sc, _, obs_direct = _direct_combine(50.0, 56.0, 40.0, True, True)
    assert obs_direct  # 直算确为观察档
    assert v.recommendation == "buy" and v.confidence == 0.4
    assert v.stance == "bull"
    assert v.score == pytest.approx(sc, abs=1e-9)
    assert v.extras["action"] == "观察" and v.extras["tier"] == "observe"
    assert not v.extras.get("fail_closed")


def test_row3_score_below_is_legal_hold():
    v = AGENT().propose(_ctx(tech=10.0, rps_mix=10.0, env_score=10.0))
    v.validate()
    sc, _, _ = _direct_combine(10.0, 10.0, 10.0, True, True)
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.stance == "neutral"
    assert v.score == pytest.approx(sc, abs=1e-9)
    assert v.extras["no"] == "score_below"
    assert not v.extras.get("fail_closed")  # 合法判定，非 fail-closed


def test_row4_env_gate_veto_keeps_score():
    """闸门否决：hold 但 score 承载 sc 原值（env 分仍计入打分）。"""
    v = AGENT().propose(_ctx(tech=80.0, rps_mix=83.0, env_score=50.0,
                             env_safe=False))
    v.validate()
    sc, _, _ = _direct_combine(80.0, 83.0, 50.0, False, True)
    assert sc == pytest.approx(75.0, abs=1e-9)  # sc 与闸门无关
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.score == pytest.approx(sc, abs=1e-9)
    assert v.extras["action"] == "回避（环境闸门）"
    assert v.extras["veto"] == "env_gate"
    assert not v.extras.get("fail_closed")


def test_row4_gate_off_variant_does_not_veto():
    """gate_off 变体：safe=False 不否决，同格出买入（env 分仍计入）。"""
    v = QpzmGateOff().propose(_ctx(tech=80.0, rps_mix=83.0, env_score=50.0,
                                   env_safe=False))
    v.validate()
    assert v.recommendation == "buy" and v.confidence == 0.7
    assert v.extras["gate_on"] is False
    assert v.extras.get("veto") is None


def test_row5_rps_missing_hr_semantics():
    """hr 压死语义：RPS 缺维伪分 100 过阈也永不信号（score 承载伪分）。"""
    v = AGENT().propose(_ctx(tech=100.0, rps_mix=None, env_score=100.0))
    v.validate()
    sc, _, _ = _direct_combine(100.0, None, 100.0, True, True)
    assert sc == pytest.approx(100.0, abs=1e-9)  # renorm 伪分 = (35+15)/0.5
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.stance == "neutral"
    assert v.score == pytest.approx(sc, abs=1e-9)  # 伪分原值，信息保全
    assert v.extras["no"] == "rps_missing"
    assert not v.extras.get("fail_closed")


def test_row6_min_bars_zero_score():
    """不入池：score=0.0（本体 wsum=0 → sc=NaN，Agent 0.0 承载）。"""
    v = AGENT().propose(_ctx(tech=80.0, rps_mix=83.0, eligible=False))
    v.validate()
    sc, _, _ = _direct_combine(80.0, 83.0, 50.0, True, False)
    assert math.isnan(sc)
    assert v.recommendation == "hold" and v.score == 0.0
    assert v.extras["no"] == "min_bars"
    assert not v.extras.get("fail_closed")


def test_row6_tech_nan_forces_el_false():
    """tech 缺维（None≡NaN）强制 el=False → 池外语义（本体 tech.notna()）。"""
    v = AGENT().propose(_ctx(tech=None, rps_mix=80.0, eligible=True))
    v.validate()
    assert v.recommendation == "hold" and v.score == 0.0
    assert v.extras["no"] == "min_bars"
    assert not v.extras.get("fail_closed")


def test_never_sell_across_grid():
    """qpzm 纯多头截面筛选：全网格任何场景 recommendation ∈ {buy, hold}。"""
    agent = AGENT()
    for combo in COMBOS:
        tech, rps_mix, env_score, env_safe, eligible = combo
        v = agent.propose(_ctx(tech=tech, rps_mix=rps_mix,
                               env_score=env_score, env_safe=env_safe,
                               eligible=eligible))
        assert v.recommendation in ("buy", "hold"), combo


def test_action_buynotify_roundtrip():
    """反解一致性：action∈{买入,观察} ⇔ recommendation="buy"（S3 对账口径）。"""
    agent = AGENT()
    for combo in COMBOS:
        tech, rps_mix, env_score, env_safe, eligible = combo
        if tech is None or rps_mix is None or not eligible:
            continue
        v = agent.propose(_ctx(tech=tech, rps_mix=rps_mix,
                               env_score=env_score, env_safe=env_safe,
                               eligible=eligible))
        is_signal = v.extras.get("action") in ("买入", "观察")
        assert is_signal == (v.recommendation == "buy"), combo


# ---------------------------------------------------------------------- #
# ④ 变体阈值敏感（th7050 翻档判例）
# ---------------------------------------------------------------------- #
def test_th_alt_variant_threshold_flips():
    """sc=72：现行档观察，th7050 档买入（对拍同值翻档）。"""
    tech, rps, env = 70.0, 70.0, 76.0   # (24.5+11.4+17.5)/0.75 = 71.2
    sc, _, _ = _direct_combine(tech, rps, env, True, True, buy_th=70.0)
    assert 70.0 <= sc < 75.0
    v_base = AGENT().propose(_ctx(tech=tech, rps_mix=rps, env_score=env))
    v_alt = QpzmThAlt().propose(_ctx(tech=tech, rps_mix=rps, env_score=env))
    assert v_base.recommendation == "buy" and v_base.extras["action"] == "观察"
    assert v_alt.recommendation == "buy" and v_alt.extras["action"] == "买入"
    assert v_base.score == pytest.approx(v_alt.score, abs=1e-12)


# ---------------------------------------------------------------------- #
# ⑤ fail-closed：缺键 / 类型坏 / 非映射 / 异常兜底（propose 永不 raise）
# ---------------------------------------------------------------------- #
def _assert_fail_closed(v, reason_kw):
    v.validate()
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.stance == "neutral" and v.score == 0.0
    assert v.extras.get("fail_closed") is True
    assert reason_kw in v.rationale


FIELD_KEYS = ("tech", "rps_mix", "n_avail", "env_score", "env_safe",
              "eligible")


@pytest.mark.parametrize("key", FIELD_KEYS)
def test_fail_closed_missing_each_key(key):
    ctx = _ctx(tech=80.0, rps_mix=83.0)
    del ctx["fields"][key]
    _assert_fail_closed(AGENT().propose(ctx), "缺键")


@pytest.mark.parametrize("key,bad", [
    ("tech", "80"),            # 字符串不是分
    ("rps_mix", [80.0]),
    ("n_avail", "3"),
    ("n_avail", True),         # bool 不是周期数
    ("env_score", None),       # env_score 无缺维语义（NaN 由装配层注入 float）
    ("env_score", "60"),
    ("env_safe", 1),           # int 不是 bool
    ("env_safe", None),
    ("eligible", 1),
    ("eligible", None),
    ("tech", True),            # bool 不是分
])
def test_fail_closed_bad_type_each_key(key, bad):
    ctx = _ctx(tech=80.0, rps_mix=83.0)
    ctx["fields"][key] = bad
    _assert_fail_closed(AGENT().propose(ctx), "类型坏")


def test_fail_closed_fields_missing_or_not_mapping():
    _assert_fail_closed(AGENT().propose({"symbol": SYMBOL}), "fields 缺失")
    _assert_fail_closed(AGENT().propose(
        {"symbol": SYMBOL, "fields": [1, 2]}), "fields 缺失")


def test_exception_fallback_never_raises():
    """任意异常 → fail-closed 中性 Verdict，propose 绝不 raise。"""
    v = AGENT().propose(None)  # context.get 即炸 → 兜底
    assert v.recommendation == "hold" and v.confidence == 0.0
    assert v.extras.get("fail_closed") is True
    assert "绝不 raise" in v.rationale


def test_none_is_nan_not_fail_closed():
    """None ≡ NaN 缺维（JSON 口径）：tech/rps_mix=None 走公式，不 fail-closed。"""
    v = AGENT().propose(_ctx(tech=None, rps_mix=None))
    v.validate()
    assert v.recommendation == "hold" and v.extras["no"] == "min_bars"
    assert not v.extras.get("fail_closed")


# ---------------------------------------------------------------------- #
# ⑥ 无写副作用 + fresh import 零 qpzm 依赖 + 源码红线
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


@pytest.mark.parametrize("combo", [
    (80.0, 83.0, 50.0, True, True),     # 买入格
    (80.0, None, 50.0, False, True),    # rps_missing 格
    (80.0, 83.0, 50.0, True, False),    # min_bars 格
])
def test_propose_has_no_write_side_effects(combo):
    tech, rps_mix, env_score, env_safe, eligible = combo
    ctx = _NoWriteMapping(_ctx(tech=tech, rps_mix=rps_mix,
                               env_score=env_score, env_safe=env_safe,
                               eligible=eligible))
    v = AGENT().propose(ctx)
    v.validate()
    assert ctx.writes == []
    # Agent 实例不持有订单/账本/执行客户端
    for attr in vars(AGENT()).values():
        for forbidden in ("order", "account", "ledger", "exec", "client"):
            assert forbidden not in str(type(attr)).lower()


def test_module_import_does_not_pull_qpzm_chain():
    """插件模块导入期零 qpzm 依赖（连 pandas/numpy 都不依赖）——定案 8：
    Agent 只复刻单格公式 + 常量类属性，导入期绝对干净。"""
    before = set(sys.modules)
    fresh = _load_module(
        "qpzm_agent_fresh_import_check",
        os.path.join(_AGENTS_DIR, "qpzm_agent.py"))
    assert hasattr(fresh, "QpzmAgent")
    new = set(sys.modules) - before
    pulled = [k for k in new if k.startswith(
        ("qpzm", "pandas", "numpy", "rps_"))]
    assert not pulled, "模块导入期拉起了禁用依赖: %s" % pulled


def test_module_source_free_of_execution_chain_and_body():
    """模块源码不引用执行链/账本/下单/本体（红线静态断言）。"""
    src = open(os.path.join(_AGENTS_DIR, "qpzm_agent.py"),
               encoding="utf-8").read()
    for forbidden in ("execution_engine", "place_order", "cancel_order",
                      "order_book", "ledger_write", "write_ledger",
                      "account", "broker", "trade_api",
                      "import qpzm_backtest", "import qpzm_daily_batch",
                      "import rps_resonance", "from qpzm_backtest",
                      "import pandas", "import numpy"):
        assert forbidden not in src, "源码含禁用标识: %s" % forbidden


def test_live_diff_freeze_registered_in_docstring():
    """live/replay 已知差异冻结登记（R3 风险：不得当 bug 修）。"""
    src = open(os.path.join(_AGENTS_DIR, "qpzm_agent.py"),
               encoding="utf-8").read()
    for token in ("K233", "次新降级", "jev_mode=\"missing\"", "hr 压死",
                  "cross_section_context"):
        assert token in src, "docstring 缺冻结登记: %s" % token
