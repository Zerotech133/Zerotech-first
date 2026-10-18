# -*- coding: utf-8 -*-
"""影子主链防拖死护栏测试（设计稿 §5.2 过渡缓解：单策略产出超时包裹）。

背景：M3 受理沙箱只在插件受理时挡一轮，运行期若某插件 propose 在特定数据
上死循环，进程内调用会拖死整个影子 run（周一 17:10 首跑挂死风险）。
本组锁死 run_shadow 侧的护栏契约：

1. 正常策略：Verdict 原样透传；
2. 死循环策略：超时返回 None + 不抛（fail-open），用时 >= timeout 且有界；
3. 异常策略：单策略粒度 fail-open（对齐 done 分支既有 strategy 域降级风格）；
4. timeout_s 可配置（传参 + monkeypatch PROPOSE_TIMEOUT_S 双通道）；
5. done 分支接线 smoke：死循环插件不拖垮其余策略的产出。

纪律：全部 tmp 隔离（本测试组不落任何 agent_platform/data/ 文件）；
死循环用例用小 timeout（0.5~1s），不拖测试节奏。
"""
import sys
import time
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import run_shadow  # noqa: E402
from strategy_registry import StrategyAgent, StrategyError, Verdict  # noqa: E402


class _OkAgent(StrategyAgent):
    """正常策略：返回合法 Verdict。"""

    strategy_id = "guard_ok"
    stance = "neutral"

    def propose(self, context):
        return Verdict(strategy_id=self.strategy_id, symbol=context["symbol"],
                       recommendation="hold", confidence=0.5)


class _DeadloopAgent(StrategyAgent):
    """死循环策略：模拟运行期永不返回的 propose（受理试跑挡不住的形态）。

    用 while True + sleep 而非纯 CPU 自旋：超时语义完全一致（线程永不结束
    → join 超时 → fail-open），但释放 GIL——测试产生的僵尸线程会存活到
    pytest 进程退出，纯自旋会全程拖慢全仓其余测试（判例：全量跑被
    pytest-timeout 60s 硬杀在无关用例上）。"""

    strategy_id = "guard_deadloop"
    stance = "neutral"

    def propose(self, context):
        while True:
            time.sleep(0.05)


class _BoomAgent(StrategyAgent):
    """异常策略：propose 抛 StrategyError。"""

    strategy_id = "guard_boom"
    stance = "neutral"

    def propose(self, context):
        raise StrategyError("guard_boom 故意抛错")


class _SleepAgent(StrategyAgent):
    """慢策略：sleep 超过指定秒数后返回合法 Verdict（用于可配置性验证）。"""

    strategy_id = "guard_slow"
    stance = "neutral"

    def __init__(self, seconds):
        self.seconds = seconds

    def propose(self, context):
        time.sleep(self.seconds)
        return Verdict(strategy_id=self.strategy_id, symbol=context["symbol"],
                       recommendation="buy", confidence=0.8)


def _registry(*agents):
    from strategy_registry import StrategyRegistry
    r = StrategyRegistry()
    for a in agents:
        r.register(a)
    return r


_CTX = {"symbol": "600519", "closes": [100.0, 101.0, 102.0]}


# ---------------------------------------------------------------- 正常透传

def test_normal_strategy_verdict_passthrough():
    """契约 1：正常策略经超时包裹后 Verdict 原样透传（字段逐一相等）。"""
    reg = _registry(_OkAgent())
    v = run_shadow._propose_with_timeout(reg, "guard_ok", _CTX, timeout_s=5.0)
    assert isinstance(v, Verdict)
    assert v.strategy_id == "guard_ok"
    assert v.recommendation == "hold" and v.confidence == 0.5


def test_passthrough_matches_direct_run():
    """透传语义：包裹结果与直接 registry.run 的产出字段完全一致。"""
    reg = _registry(_OkAgent())
    v = run_shadow._propose_with_timeout(reg, "guard_ok", _CTX, timeout_s=5.0)
    direct = reg.run("guard_ok", _CTX)
    assert (v.strategy_id, v.symbol, v.recommendation, v.confidence, v.stance) == \
           (direct.strategy_id, direct.symbol, direct.recommendation,
            direct.confidence, direct.stance)


# ---------------------------------------------------------------- 死循环超时

def test_deadloop_times_out_returns_none_no_raise():
    """契约 2：死循环策略超时返回 None，不抛异常（fail-open）。"""
    reg = _registry(_DeadloopAgent())
    t0 = time.monotonic()
    v = run_shadow._propose_with_timeout(reg, "guard_deadloop", _CTX, timeout_s=1.0)
    elapsed = time.monotonic() - t0
    assert v is None
    assert 1.0 <= elapsed <= 6.0  # >= timeout 且有界（CI 余量 5s）


def test_deadloop_does_not_drag_others():
    """契约 5 前置：同一 registry 里死循环 + 正常策略，包裹逐策略调用互不影响。"""
    reg = _registry(_DeadloopAgent(), _OkAgent())
    v_dead = run_shadow._propose_with_timeout(reg, "guard_deadloop", _CTX, timeout_s=0.5)
    v_ok = run_shadow._propose_with_timeout(reg, "guard_ok", _CTX, timeout_s=0.5)
    assert v_dead is None
    assert v_ok is not None and v_ok.strategy_id == "guard_ok"


# ---------------------------------------------------------------- 异常 fail-open

def test_exception_strategy_fail_open_returns_none():
    """契约 3：策略抛 StrategyError → 单策略 fail-open，返回 None 不上抛。"""
    reg = _registry(_BoomAgent())
    v = run_shadow._propose_with_timeout(reg, "guard_boom", _CTX, timeout_s=5.0)
    assert v is None


def test_exception_fail_open_matches_done_branch_degradation_style():
    """口径对表：done 分支既有风格是 strategy 域整体降级不碰主链（外层
    try/except print warn），故包裹选单策略粒度 fail-open + 诊断输出，
    与之同向而非 fail-visible 上抛。"""
    reg = _registry(_BoomAgent(), _OkAgent())
    # 不抛即证据：包裹内部消化异常
    vs = run_shadow._collect_verdicts(reg, _CTX, timeout_s=5.0)
    assert [v.strategy_id for v in vs] == ["guard_ok"]  # 异常策略被剔除，其余照常


# ---------------------------------------------------------------- 可配置性

def test_timeout_s_param_configurable():
    """契约 4a：传参通道——慢策略在参数 timeout 内/外的两种结果。"""
    reg = _registry(_SleepAgent(1.5))
    v_out = run_shadow._propose_with_timeout(reg, "guard_slow", _CTX, timeout_s=0.3)
    assert v_out is None  # 0.3s < 1.5s → 跳过
    v_in = run_shadow._propose_with_timeout(reg, "guard_slow", _CTX, timeout_s=5.0)
    assert v_in is not None and v_in.recommendation == "buy"


def test_default_timeout_monkeypatchable(monkeypatch):
    """契约 4b：常量通道——monkeypatch PROPOSE_TIMEOUT_S 对无参调用生效
    （None 哨兵设计：默认参数绑定会锁死 def 期常量）。"""
    monkeypatch.setattr(run_shadow, "PROPOSE_TIMEOUT_S", 0.3)
    reg = _registry(_SleepAgent(1.5))
    v = run_shadow._propose_with_timeout(reg, "guard_slow", _CTX)
    assert v is None


def test_default_timeout_constant_value():
    """生产默认值=10.0s（模块级常量，便于运维调参与 patch）。"""
    assert run_shadow.PROPOSE_TIMEOUT_S == 10.0


# ---------------------------------------------------------------- done 分支接线 smoke

def test_collect_verdicts_deadloop_not_dragging_rest():
    """契约 5：done 分支接线 smoke——死循环插件 + 正常插件共存，包裹收集
    后正常策略仍产出（死循环被剔除，顺序保持 list_ids 序）。"""
    reg = _registry(_DeadloopAgent(), _OkAgent(), _BoomAgent())
    t0 = time.monotonic()
    vs = run_shadow._collect_verdicts(reg, _CTX, timeout_s=0.5)
    elapsed = time.monotonic() - t0
    assert [v.strategy_id for v in vs] == ["guard_ok"]
    assert elapsed <= 4.0  # 两次跳过各 0.5s + 余量，绝不等死循环跑完


def test_collect_verdicts_all_healthy_passthrough():
    """全健康策略：_collect_verdicts 与旧列表推导 [reg.run(...)] 等价。"""
    reg = _registry(_OkAgent(), _SleepAgent(0.0))
    vs = run_shadow._collect_verdicts(reg, _CTX, timeout_s=5.0)
    assert [v.strategy_id for v in vs] == reg.list_ids()
    assert all(isinstance(v, Verdict) for v in vs)


def test_collect_verdicts_none_does_not_reach_record(monkeypatch, tmp_path):
    """落账面契约：None 不得进入 record_verdicts 的 verdicts 批（坏批零副作用）。

    用死循环策略验证：收集结果只含健康策略，record_verdicts（root 指向 tmp
    隔离根）正常落账 recorded=1——即 done 分支现有落账链路在包裹后不变形。"""
    from strategy_registry import record_verdicts
    reg = _registry(_DeadloopAgent(), _OkAgent())
    vs = run_shadow._collect_verdicts(reg, _CTX, timeout_s=0.5)
    rep = record_verdicts(vs, root=tmp_path, trade_date="2026-10-18",
                          run_id="guard-test")
    assert rep["recorded"] == 1 and rep["replayed"] == 0
