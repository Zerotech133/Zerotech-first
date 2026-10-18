"""T05 Prometheus 闭环指标测试：三指标注册 + 挂点取值变化
（CaseWriter.on_ack → aw_loop_conversion_total；run_cycle → aw_decisions_total；
GraduationService.apply_weights → aw_graduation_weight）。全 mock 无真 DB。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.agent_platform import metrics as aw_metrics
from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.db import decision_to_row
from zhice_os_v2.agent_platform.learning import CardAck, CaseWriter, GraduationService
from zhice_os_v2.agent_platform.methods import MethodVote, WatchContext
from zhice_os_v2.agent_platform.models import AwBase, AwDecisionModel
from zhice_os_v2.agent_platform.runtime.shard import ShardPolicy
from zhice_os_v2.agent_platform.runtime.watch_agent import WatchAgent
from zhice_os_v2.agent_platform.tests_support import synth_bars

prometheus_client = pytest.importorskip("prometheus_client")


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AwBase.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _sell_decision(decision_id: str = "aw-met001") -> AgentDecision:
    return AgentDecision(
        decision_id=decision_id, ts_code="BTC/USDT", market="CRYPTO",
        action="SELL", score_sell=0.55, score_hold=0.3,
        votes=[MethodVote("chan", "SELL", 0.7, ["背驰"])],
        regime_risk_off=False, arbitration_path="WEIGHTED",
    )


def _counter_value(name: str, labels: dict) -> float:
    """读全局 REGISTRY 中某 Counter 指定 label 的当前值（未 inc 过=0）。"""
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value(f"{name}_total", dict(labels))
    return float(value) if value is not None else 0.0


def _gauge_value(name: str, labels: dict) -> float | None:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, dict(labels))


class TestMetricRegistration:
    def test_three_closed_loop_metrics_registered(self):
        """三闭环指标经懒注册进入 REGISTRY。

        prometheus_client 中带 label 的 Counter 在首次 labels().inc() 前
        samples 为空 → 用 describe() 校验 family 注册存在性。
        """
        aw_metrics._ensure_metrics()
        from prometheus_client import REGISTRY

        families = {m.name for m in REGISTRY.collect()}
        assert "aw_loop_conversion" in families          # Counter family（样本 _total）
        assert "aw_decisions" in families                # Counter family（样本 _total）
        assert "aw_graduation_weight" in families        # Gauge family


class TestAckConversionHook:
    def test_on_ack_confirm_incs_confirmed(self, session_factory):
        with session_factory() as s:
            s.add(decision_to_row(_sell_decision()))
            s.commit()
        before = _counter_value("aw_loop_conversion", {"result": "confirmed"})
        writer = CaseWriter(session_factory=session_factory)
        writer.on_ack(CardAck(decision_id="aw-met001", operator="qa",
                              action="CONFIRM_SELL"))
        after = _counter_value("aw_loop_conversion", {"result": "confirmed"})
        assert after == pytest.approx(before + 1.0)

    def test_on_ack_ignore_incs_ignored(self, session_factory):
        with session_factory() as s:
            s.add(decision_to_row(_sell_decision()))
            s.commit()
        before = _counter_value("aw_loop_conversion", {"result": "ignored"})
        writer = CaseWriter(session_factory=session_factory)
        writer.on_ack(CardAck(decision_id="aw-met001", operator="qa",
                              action="IGNORE"))
        after = _counter_value("aw_loop_conversion", {"result": "ignored"})
        assert after == pytest.approx(before + 1.0)

    def test_on_ack_unknown_decision_no_inc(self, session_factory):
        before = _counter_value("aw_loop_conversion", {"result": "confirmed"})
        writer = CaseWriter(session_factory=session_factory)
        assert writer.on_ack(CardAck(decision_id="aw-none", operator="qa",
                                     action="CONFIRM_SELL")) is None
        assert _counter_value("aw_loop_conversion", {"result": "confirmed"}) \
            == pytest.approx(before)


class TestDecisionHook:
    def test_run_cycle_incs_decisions_by_action(self):
        df = synth_bars(200, seed=42)
        ctxs = [WatchContext(
            ts_code="TEST/USDT", market="CRYPTO",
            snapshot_tail=df.tail(3), bars_df=df,
            regime_risk_off=False, memory_hints=[], position_value=0.0,
        )]
        agent = WatchAgent(
            shard=ShardPolicy(market="CRYPTO", symbols=["TEST/USDT"], snapshot_hz="5s"),
            cfg=AgentPlatformConfig(),
        )
        decisions = agent.run_cycle(ctxs=ctxs)
        assert len(decisions) == 1
        action = decisions[0].action
        before = _counter_value("aw_decisions", {"action": action})
        agent.run_cycle(ctxs=ctxs)
        assert _counter_value("aw_decisions", {"action": action}) \
            == pytest.approx(before + 1.0)


class TestGraduationWeightHook:
    def test_apply_weights_sets_gauge(self, session_factory):
        """毕业应用后 Gauge 反映权重现值（chan 1.0 → 无样本 HOLD 不变 → 手造报告 1.5）。"""
        svc = GraduationService(session_factory=session_factory)
        svc.apply_weights(reports=[{"method_id": "chan", "market": "CRYPTO",
                                    "sample_size": 0, "mean_fwd_ret": None,
                                    "decision": "HOLD", "old_weight": 1.0,
                                    "new_weight": 1.5, "reason": "test"}])
        assert _gauge_value("aw_graduation_weight", {"method": "chan"}) \
            == pytest.approx(1.5)


class TestMetricsServerPort:
    def test_default_port_9106_not_9101(self):
        """端口约定：9101 被 jev_gate 占用 → T05 缺省 9106。"""
        assert aw_metrics.DEFAULT_METRICS_PORT == 9106
