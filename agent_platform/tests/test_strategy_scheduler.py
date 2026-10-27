# -*- coding: utf-8 -*-
"""独立多策略并行调度器测试。"""

import sys
import threading
import time
from pathlib import Path


PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_registry import StrategyAgent, StrategyRegistry, Verdict  # noqa: E402
from strategy_scheduler import (  # noqa: E402
    MultiStrategyScheduler,
    run_strategies,
)


class _Agent(StrategyAgent):
    def __init__(self, strategy_id, action=None, lifecycle_state="active"):
        self.strategy_id = strategy_id
        self.action = action
        self.lifecycle_state = lifecycle_state

    def propose(self, context):
        if self.action is not None:
            return self.action(context)
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="hold",
            confidence=0.5,
        )


def _registry(*agents):
    registry = StrategyRegistry()
    for agent in agents:
        registry.register(agent)
    return registry


def test_all_strategies_succeed_with_uniform_results():
    registry = _registry(_Agent("s1"), _Agent("s2"), _Agent("s3"))

    results = run_strategies(
        registry,
        ["s1", "s2", "s3"],
        {"symbol": "600519"},
        timeout_s=0.2,
        max_workers=3,
    )

    assert [result["strategy_id"] for result in results] == ["s1", "s2", "s3"]
    assert all(result["status"] == "success" for result in results)
    assert all(result["elapsed_ms"] >= 0 for result in results)
    assert all(
        result["verdict"].strategy_id == result["strategy_id"] for result in results
    )
    assert all(result["error"] is None for result in results)
    assert all(
        set(result) == {"strategy_id", "status", "elapsed_ms", "verdict", "error"}
        for result in results
    )


def test_one_exception_does_not_block_other_strategies():
    def fail(_context):
        raise ValueError("插件故障")

    registry = _registry(_Agent("bad", fail), _Agent("good"))

    results = run_strategies(
        registry,
        ["bad", "good"],
        {"symbol": "600519"},
        timeout_s=0.2,
        max_workers=2,
    )

    assert results[0]["status"] == "error"
    assert "插件故障" in results[0]["error"]
    assert results[1]["status"] == "success"
    assert results[1]["verdict"].strategy_id == "good"


def test_per_strategy_timeout_does_not_block_other_strategies():
    def slow(_context):
        time.sleep(0.15)
        return Verdict("slow", "600519", "hold", 0.5)

    registry = _registry(_Agent("slow", slow), _Agent("fast"))
    started = time.perf_counter()
    results = MultiStrategyScheduler(registry, max_workers=2).run(
        ["slow", "fast"],
        {"symbol": "600519"},
        strategy_timeouts={"slow": 0.01, "fast": 0.2},
    )

    assert results[0]["status"] == "timeout"
    assert "超时" in results[0]["error"]
    assert results[1]["status"] == "success"
    assert time.perf_counter() - started < 0.1


def test_overall_timeout_marks_remaining_strategies_timeout():
    def slow(_context):
        time.sleep(0.15)
        return Verdict("slow", "600519", "hold", 0.5)

    registry = _registry(_Agent("s1", slow), _Agent("s2", slow))
    results = run_strategies(
        registry,
        ["s1", "s2"],
        {"symbol": "600519"},
        timeout_s=1.0,
        overall_timeout_s=0.01,
        max_workers=2,
    )

    assert [result["status"] for result in results] == ["timeout", "timeout"]
    assert all("overall timeout" in result["error"] for result in results)


def test_lifecycle_rejection_and_deprecated_replay_only():
    registry = _registry(
        _Agent("experimental", lifecycle_state="experimental"),
        _Agent("active", lifecycle_state="active"),
        _Agent("deprecated", lifecycle_state="deprecated"),
    )

    rejected = run_strategies(
        registry,
        ["experimental", "active", "deprecated"],
        {"symbol": "600519"},
        allowed_lifecycle=("active",),
        timeout_s=0.2,
        max_workers=3,
    )
    assert [result["status"] for result in rejected] == [
        "rejected",
        "success",
        "rejected",
    ]
    assert "experimental" in rejected[0]["error"]
    assert "deprecated" in rejected[2]["error"]

    replayed = run_strategies(
        registry,
        ["deprecated"],
        {"symbol": "600519"},
        timeout_s=0.2,
        replay=True,
        max_workers=1,
    )
    assert replayed[0]["status"] == "success"


def test_max_workers_limits_active_strategy_count():
    lock = threading.Lock()
    active = 0
    max_active = 0

    def observe(_context):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return Verdict("placeholder", "600519", "hold", 0.5)

    agents = []
    for index in range(4):
        sid = "s%d" % index

        def action(context, sid=sid):
            verdict = observe(context)
            verdict.strategy_id = sid
            return verdict

        agents.append(_Agent(sid, action))
    registry = _registry(*agents)

    results = run_strategies(
        registry,
        [agent.strategy_id for agent in agents],
        {"symbol": "600519"},
        timeout_s=0.5,
        max_workers=2,
    )

    assert all(result["status"] == "success" for result in results)
    assert max_active <= 2
    assert max_active == 2
