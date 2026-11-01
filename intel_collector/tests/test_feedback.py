# -*- coding: utf-8 -*-
"""分析→采集反馈环测试：SLA 违约/滞后/无数据 → 加速补采/熔断；PII 命中 → 降级+复核。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from core.feedback import (
    FeedbackAction, FeedbackActionType, FeedbackEngine, SlaFeedbackPolicy,
    apply_actions, compute_pii_feedback, compute_sla_feedback,
)
from core.quality_sla import STATE_BREACH, STATE_NO_DATA, STATE_OK, STATE_STALE, SlaReport
from registry.source_registry import SourceRegistry, SourceSpec
from core.scheduler import Scheduler
from core.contracts import UseCase


def _report(sid, state, fresh=30, max_fail=5, age=None):
    return SlaReport(
        source_id=sid, freshness_minutes=fresh, max_failures=max_fail,
        observation_count=1 if state != STATE_NO_DATA else 0,
        empty_count=0, empty_ratio=0.0, last_collected_at="2026-11-01T00:00:00Z",
        age_minutes=age, state=state, circuit="closed", consecutive_failures=0,
        note="",
    )


# ---------- 纯计算：SLA ----------
def test_no_data_expedite():
    actions, strikes = compute_sla_feedback({"a": _report("a", STATE_NO_DATA)})
    assert any(a.action == FeedbackActionType.EXPEDITE_RETRY for a in actions)
    assert strikes["a"] == 0


def test_stale_expedite():
    actions, strikes = compute_sla_feedback({"a": _report("a", STATE_STALE, age=45)})
    assert any(a.action == FeedbackActionType.EXPEDITE_RETRY for a in actions)
    assert strikes["a"] == 0


def test_ok_no_action():
    actions, strikes = compute_sla_feedback({"a": _report("a", STATE_OK, age=10)})
    assert actions == []
    assert strikes["a"] == 0


def test_breach_expedite_and_strike():
    actions, strikes = compute_sla_feedback({"a": _report("a", STATE_BREACH, age=120)})
    assert any(a.action == FeedbackActionType.EXPEDITE_RETRY for a in actions)
    assert strikes["a"] == 1
    # 未达阈值，不开闸
    assert not any(a.action == FeedbackActionType.OPEN_CIRCUIT for a in actions)


def test_consecutive_breach_opens_circuit():
    policy = SlaFeedbackPolicy(breach_open_threshold=3)
    strikes: dict[str, int] = {}
    last_actions = []
    for _ in range(3):
        acts, strikes = compute_sla_feedback(
            {"a": _report("a", STATE_BREACH, age=120)}, strikes, policy)
        last_actions = acts
    # 第 3 次触发开闸 + 复核，strike 重置
    assert any(a.action == FeedbackActionType.OPEN_CIRCUIT for a in last_actions)
    assert any(a.action == FeedbackActionType.FLAG_REVIEW for a in last_actions)
    assert strikes["a"] == 0


def test_ok_resets_strike():
    strikes = {"a": 2}
    _, strikes = compute_sla_feedback({"a": _report("a", STATE_OK, age=10)}, strikes)
    assert strikes["a"] == 0


# ---------- 纯计算：PII ----------
def test_pii_downgrade_and_review():
    actions = compute_pii_feedback({"a": 3})
    assert any(a.action == FeedbackActionType.DOWNGRADE_RIGHTS for a in actions)
    assert any(a.action == FeedbackActionType.FLAG_REVIEW for a in actions)


def test_pii_zero_no_action():
    assert compute_pii_feedback({"a": 0}) == []


# ---------- 副作用：apply ----------
def _registry_with_training():
    reg = SourceRegistry()  # 内存，不落盘
    spec = SourceSpec(
        source_id="a", name="a", category="open_source_intel",
        owner="intel(军情司·INTEL0)",
        rights={UseCase.INTERNAL_RETENTION.value: True, UseCase.TRAINING.value: True},
        sla={"freshness_minutes": 30, "max_failures": 3}, enabled=True,
    )
    reg.register(spec)
    return reg


def test_apply_expedite_forces_due():
    reg = _registry_with_training()
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    sched.due_sources()  # 初始化 next_due
    apply_actions([FeedbackAction("a", FeedbackActionType.EXPEDITE_RETRY, "t")],
                 registry=reg, scheduler=sched)
    # 推进时钟后，该源应仍 immediately due（next_due == now）
    assert sched.due_sources()[0].source_id == "a"


def test_apply_open_circuit_disables():
    reg = _registry_with_training()
    apply_actions([FeedbackAction("a", FeedbackActionType.OPEN_CIRCUIT, "t")], registry=reg)
    assert reg.get("a").enabled is False


def test_apply_downgrade_revokes_training():
    reg = _registry_with_training()
    apply_actions([FeedbackAction("a", FeedbackActionType.DOWNGRADE_RIGHTS, "t")], registry=reg)
    assert reg.get("a").rights.get(UseCase.TRAINING.value) is False
    # 内部留存不受影响
    assert reg.get("a").rights.get(UseCase.INTERNAL_RETENTION.value) is True


def test_apply_review_flag():
    reg = _registry_with_training()
    apply_actions([FeedbackAction("a", FeedbackActionType.FLAG_REVIEW, "需复核")], registry=reg)
    assert reg.get("a").health.get("review_flag") == "需复核"


# ---------- 会话态引擎：闭环集成 ----------
def test_engine_run_resets_strike_after_open():
    reg = _registry_with_training()
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    eng = FeedbackEngine(registry=reg, scheduler=sched,
                         policy=SlaFeedbackPolicy(breach_open_threshold=2))
    # 连续两次 breach → 第二次应触发开闸（禁用源）
    eng.run({"a": _report("a", STATE_BREACH, age=120)}, {})
    assert reg.get("a").enabled is True  # 第一次仅加速
    eng.run({"a": _report("a", STATE_BREACH, age=120)}, {})
    assert reg.get("a").enabled is False  # 第二次达阈值开闸
    assert reg.get("a").health.get("review_flag") is not None
    # 第 3 次：源已禁用，due_sources 不再派发；但反馈计算仍基于传入报告
    actions = eng.run({"a": _report("a", STATE_BREACH, age=120)}, {})
    # 已开闸后 strike 重置，不应再重复开闸动作
    assert not any(a.action == FeedbackActionType.OPEN_CIRCUIT for a in actions)
