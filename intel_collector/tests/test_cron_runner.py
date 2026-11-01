# -*- coding: utf-8 -*-
"""统一 cron 实体测试：到期派发 / 推进 next_due / 分析反馈环 / run_forever 注入。不触网。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from core.cron_runner import CronRunner, TickReport
from core.feedback import FeedbackActionType, FeedbackEngine
from core.quality_sla import STATE_BREACH, SlaReport
from registry.source_registry import SourceRegistry, SourceSpec
from core.scheduler import Scheduler
from core.contracts import UseCase


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def collect_source(self, source_id, now):
        self.calls.append((source_id, now))
        return {"ok": True, "n": 1, "reason": ""}


def _reg_with(sids, fresh=30):
    reg = SourceRegistry()
    for sid in sids:
        reg.register(SourceSpec(
            source_id=sid, name=sid, category="open_source_intel",
            owner="intel(军情司·INTEL0)",
            rights={UseCase.INTERNAL_RETENTION.value: True}, enabled=True,
            sla={"freshness_minutes": fresh, "max_failures": 3},
        ))
    return reg


# ---------- tick：到期派发 ----------
def test_tick_dispatches_due_source():
    reg = _reg_with(["a"])
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)
    rep = runner.tick(clock["t"])
    assert rep.dispatched == ["a"]
    assert len(disp.calls) == 1


def test_tick_not_due_after_collect():
    reg = _reg_with(["a"])
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)
    runner.tick(clock["t"])
    # 同一时刻再 tick：刚采完，next_due 已推进，不再到期
    rep2 = runner.tick(clock["t"])
    assert rep2.dispatched == []


def test_tick_due_again_after_freshness():
    reg = _reg_with(["a"], fresh=30)
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)
    runner.tick(clock["t"])
    clock["t"] += timedelta(minutes=31)  # 超过 freshness
    rep = runner.tick(clock["t"])
    assert rep.dispatched == ["a"]
    assert len(disp.calls) == 2


def test_tick_dispatches_only_enabled():
    reg = _reg_with(["a", "b"])
    reg.disable("b", reason="test")
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)
    rep = runner.tick(clock["t"])
    assert rep.dispatched == ["a"]


# ---------- tick：分析反馈环注入 ----------
def test_tick_runs_feedback_when_injected():
    reg = _reg_with(["a"])
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    eng = FeedbackEngine(registry=reg, scheduler=sched)

    def analyzer(now):
        reports = {"a": SlaReport(
            source_id="a", freshness_minutes=30, max_failures=3,
            observation_count=1, empty_count=0, empty_ratio=0.0,
            last_collected_at="2026-11-01T00:00:00Z", age_minutes=120,
            state=STATE_BREACH, circuit="closed", consecutive_failures=0, note="",
        )}
        return reports, {}

    runner = CronRunner(sched, disp, analyzer=analyzer, feedback_engine=eng)
    rep = runner.tick(clock["t"])
    assert any(a.action == FeedbackActionType.EXPEDITE_RETRY for a in rep.feedback_actions)
    # 加速补采已落到 scheduler：下一刻 a 立即可到期
    assert sched.due_sources()[0].source_id == "a"


def test_tick_no_feedback_without_injection():
    reg = _reg_with(["a"])
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)  # 无 analyzer/engine
    rep = runner.tick(clock["t"])
    assert rep.feedback_actions == []


# ---------- run_forever：注入 now/sleep ----------
def test_run_forever_respects_max_ticks_and_sleep():
    reg = _reg_with(["a"], fresh=30)
    calls = []

    def now_provider():
        # 每次调用推进 60 分钟，保证每 tick 都到期
        if calls:
            calls[-1] += timedelta(minutes=60)
        else:
            calls.append(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
        return calls[-1]

    sched = Scheduler(reg, now_provider=now_provider)
    disp = FakeDispatcher()
    runner = CronRunner(sched, disp)

    slept = []
    history = runner.run_forever(poll_interval=60.0, max_ticks=3, sleep_fn=slept.append)
    assert len(history) == 3
    assert len(slept) == 2  # 第 3 次 tick 达 max_ticks 后于 sleep 前 break
    # a 在每 tick 都到期（时钟推进>freshness），故派发 3 次
    assert all(h.dispatched == ["a"] for h in history)
    assert len(disp.calls) == 3
