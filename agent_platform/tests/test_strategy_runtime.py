# -*- coding: utf-8 -*-
"""策略 Agent 最小运行时隔离门测试。"""

import sys
import time
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_registry import StrategyAgent, StrategyRegistry, Verdict  # noqa: E402
from strategy_runtime import StrategyRuntime, run_strategy  # noqa: E402


def _verdict(strategy_id, symbol="600519"):
    return Verdict(
        strategy_id=strategy_id, symbol=symbol, recommendation="hold", confidence=0.5
    )


class _Agent(StrategyAgent):
    def __init__(self, strategy_id, lifecycle_state="active", action=None):
        self.strategy_id = strategy_id
        self.lifecycle_state = lifecycle_state
        self.action = action

    def propose(self, context):
        if self.action is not None:
            return self.action(context)
        return _verdict(self.strategy_id, context["symbol"])


def _runtime(agent, timeout_s=0.2):
    registry = StrategyRegistry()
    registry.register(agent)
    return StrategyRuntime(registry, timeout_s=timeout_s)


def test_success_returns_standard_result():
    result = _runtime(_Agent("s_ok")).run("s_ok", {"symbol": "600519"})

    assert result["strategy_id"] == "s_ok"
    assert result["status"] == "success"
    assert result["elapsed_ms"] >= 0
    assert result["verdict"].strategy_id == "s_ok"
    assert result["error"] is None


@pytest.mark.parametrize("lifecycle_state", ["retired", "deprecated"])
def test_retired_and_deprecated_are_rejected_by_default(lifecycle_state):
    result = _runtime(_Agent("s_blocked", lifecycle_state)).run(
        "s_blocked", {"symbol": "600519"}
    )

    assert result["status"] == "rejected"
    assert result["verdict"] is None
    assert lifecycle_state in result["error"]


def test_deprecated_requires_explicit_replay_opt_in():
    agent = _Agent("s_replay", "deprecated")
    runtime = _runtime(agent)

    rejected = runtime.run("s_replay", {"symbol": "600519"})
    replayed = runtime.run("s_replay", {"symbol": "600519"}, allow_deprecated=True)

    assert rejected["status"] == "rejected"
    assert replayed["status"] == "success"
    assert replayed["verdict"].strategy_id == "s_replay"


def test_allow_deprecated_does_not_allow_retired():
    result = _runtime(_Agent("s_retired", "retired")).run(
        "s_retired", {"symbol": "600519"}, allow_deprecated=True
    )

    assert result["status"] == "rejected"
    assert "retired" in result["error"]


def test_context_is_recursively_readonly_and_original_is_not_mutated():
    observed = {}

    def mutate(context):
        observed["context"] = context
        with pytest.raises(TypeError):
            context["nested"]["items"][0] = "blocked"
        with pytest.raises(TypeError):
            context["nested"]["flag"] = False
        return _verdict("s_readonly")

    source = {"nested": {"items": ["kept"], "flag": True}}
    result = _runtime(_Agent("s_readonly", action=mutate)).run("s_readonly", source)

    assert result["status"] == "success"
    assert source == {"nested": {"items": ["kept"], "flag": True}}
    assert observed["context"]["nested"]["items"] == ("kept",)


def test_exception_is_fail_visible():
    def fail(_context):
        raise ValueError("插件故障")

    result = _runtime(_Agent("s_error", action=fail)).run(
        "s_error", {"symbol": "600519"}
    )

    assert result["status"] == "error"
    assert result["verdict"] is None
    assert "插件故障" in result["error"]
    assert result["elapsed_ms"] >= 0


def test_timeout_is_fail_visible_without_waiting_for_worker():
    def slow(_context):
        time.sleep(0.15)
        return _verdict("s_slow")

    started = time.perf_counter()
    result = _runtime(_Agent("s_slow", action=slow), timeout_s=0.01).run(
        "s_slow", {"symbol": "600519"}
    )
    elapsed = time.perf_counter() - started

    assert result["status"] == "timeout"
    assert result["verdict"] is None
    assert "超时" in result["error"]
    assert "无法强杀" in result["error"]
    assert result["elapsed_ms"] >= 0
    assert elapsed < 0.1


def test_readonly_context_rejects_cycles():
    context = {}
    context["self"] = context

    result = _runtime(_Agent("s_cycle")).run("s_cycle", context)

    assert result["status"] == "error"
    assert "循环引用" in result["error"]


def test_run_strategy_function_matches_runtime_contract():
    registry = StrategyRegistry()
    registry.register(_Agent("s_function", "experimental"))

    result = run_strategy(registry, "s_function", {"symbol": "600519"}, timeout_s=0.2)

    assert result["status"] == "success"
    assert result["strategy_id"] == "s_function"


def test_timeout_wrapping_uses_daemon_thread_not_executor():
    """拍板 01（2026-10-18）：机制唯一口径=裸 daemon Thread，禁 ThreadPoolExecutor。

    理由（run_shadow.py docstring 裁决）：executor worker 3.9+ 非 daemon，
    解释器退出 join 挂死——死循环策略会挂住整个进程。
    """
    import threading as _threading

    seen = {}

    def probe(_context):
        seen["thread"] = _threading.current_thread()
        return _verdict("s_probe")

    result = _runtime(_Agent("s_probe", action=probe)).run(
        "s_probe", {"symbol": "600519"}
    )

    assert result["status"] == "success"
    assert seen["thread"].daemon is True

    source = (PLATFORM / "engine" / "strategy_runtime.py").read_text(encoding="utf-8")
    assert "concurrent.futures" not in source
    assert "executor.submit" not in source
    assert "threading.Thread" in source
