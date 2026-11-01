# -*- coding: utf-8 -*-
"""调度编排测试：虚拟时钟驱动，验证到期判定/指数退避/熔断/留痕。不触网、不落盘。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from registry.source_registry import SourceRegistry, SourceSpec
from core.orchestrator import Orchestrator
from core.scheduler import Scheduler
from core.base_adapter import BaseAdapter
from core.contracts import UseCase
from store.obs_store import ObservationStore


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeAdapter(BaseAdapter):
    def __init__(self, source_id, fail=False, n=2):
        super().__init__(source_id)
        self.fail = fail
        self.n = n

    def collect(self, targets, **kwargs):
        if self.fail:
            raise RuntimeError("simulated failure")
        out = []
        for i in range(self.n):
            out.append(self.make_observation(
                entity_type="xq_feed", entity_id=f"t{i}",
                payload={"i": i}, status="complete",
                allowed_use_cases=("internal_retention",),
            ))
        return out


def _make_reg():
    reg = SourceRegistry()  # 内存，不落盘（path=None）
    reg.register(SourceSpec(
        source_id="ok_src", name="ok", category="open_source_intel",
        owner="intel(军情司·INTEL0)",
        sla={"freshness_minutes": 30, "max_failures": 3},
    ))
    reg.register(SourceSpec(
        source_id="bad_src", name="bad", category="open_source_intel",
        owner="intel(军情司·INTEL0)",
        sla={"freshness_minutes": 10, "max_failures": 3},
    ))
    return reg


def test_due_first_tick_all_enabled():
    clock = Clock(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    reg = _make_reg()
    sched = Scheduler(reg, now_provider=clock)
    due = {s.source_id for s in sched.due_sources()}
    assert due == {"ok_src", "bad_src"}, due


def test_backoff_grows_and_circuit_opens():
    clock = Clock(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    reg = _make_reg()
    orch = Orchestrator(reg, ObservationStore())
    sched = Scheduler(reg, now_provider=clock)
    jobs = {"bad_src": {"adapter": FakeAdapter("bad_src", fail=True),
                        "targets": ["x"], "bind": (UseCase.INTERNAL_RETENTION.value,)}}

    # tick1: fails=1 -> backoff = 10m * 2^1 = 20m
    res = sched.run_due(orch, jobs)
    assert res["bad_src"]["outcome"] == "failed"
    assert res["bad_src"]["consecutive_failures"] == 1
    nd1 = sched._next_due["bad_src"]
    assert nd1 == clock.t + timedelta(minutes=20), nd1

    # tick2: clock 推进到 nd1；fails=2 -> backoff = 10m*2^2=40m
    clock.t = nd1
    res = sched.run_due(orch, jobs)
    nd2 = sched._next_due["bad_src"]
    assert res["bad_src"]["consecutive_failures"] == 2
    assert nd2 == clock.t + timedelta(minutes=40), (nd2, clock.t)

    # tick3: 达到 max_failures(3) -> 注册表禁用，熔断 open
    clock.t = nd2
    res = sched.run_due(orch, jobs)
    assert res["bad_src"]["consecutive_failures"] == 3
    assert res["bad_src"]["circuit"] == "open"
    assert reg.get("bad_src").enabled is False

    # tick4: 已禁用源不在 due，跳过
    clock.t = nd2 + timedelta(minutes=100)
    res = sched.run_due(orch, jobs)
    assert "bad_src" not in res


def test_success_resets_and_schedules():
    clock = Clock(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    reg = _make_reg()
    orch = Orchestrator(reg, ObservationStore())
    sched = Scheduler(reg, now_provider=clock)
    jobs = {"ok_src": {"adapter": FakeAdapter("ok_src", fail=False),
                       "targets": ["x"], "bind": (UseCase.INTERNAL_RETENTION.value,)}}

    res = sched.run_due(orch, jobs)
    assert res["ok_src"]["outcome"] == "ok"
    assert res["ok_src"]["n"] == 2
    assert reg.get("ok_src").health.get("consecutive_failures") == 0
    assert sched._next_due["ok_src"] == clock.t + timedelta(minutes=30)
    assert sched.circuit_state("ok_src") == "closed"


def test_skipped_without_adapter():
    clock = Clock(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    reg = _make_reg()
    orch = Orchestrator(reg, ObservationStore())
    sched = Scheduler(reg, now_provider=clock)
    res = sched.run_due(orch, {})  # 未挂任何适配器
    assert res["ok_src"]["outcome"] == "skipped_no_adapter"
    assert res["bad_src"]["outcome"] == "skipped_no_adapter"


def test_ledger_records():
    clock = Clock(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    reg = SourceRegistry()  # 仅挂 ok_src，避免无适配器源产生 skipped 留痕干扰
    reg.register(SourceSpec(
        source_id="ok_src", name="ok", category="open_source_intel",
        owner="intel(军情司·INTEL0)",
        sla={"freshness_minutes": 30, "max_failures": 3},
    ))
    orch = Orchestrator(reg, ObservationStore())
    sched = Scheduler(reg, now_provider=clock)
    jobs = {"ok_src": {"adapter": FakeAdapter("ok_src", fail=False),
                       "targets": ["x"], "bind": (UseCase.INTERNAL_RETENTION.value,)}}
    sched.run_due(orch, jobs)
    assert len(sched.ledger) == 1
    assert sched.ledger[0]["source_id"] == "ok_src"
    assert sched.ledger[0]["outcome"] == "ok"
