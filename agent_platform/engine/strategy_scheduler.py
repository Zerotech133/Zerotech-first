# -*- coding: utf-8 -*-
"""独立多策略并行调度器。

本模块只负责内存中的策略调度，不写账本、不接交易执行，也不修改
Aggregator。每个策略通过 strategy_runtime 的隔离门运行，单策略异常或
超时只影响自身结果。
"""
from concurrent.futures import ThreadPoolExecutor, wait
from collections.abc import Mapping
import time

from strategy_registry import LIFECYCLE_STATES
from strategy_runtime import run_strategy


_DEFAULT_ALLOWED_LIFECYCLE = ("experimental", "active")
_REPLAY_LIFECYCLE = "deprecated"


def _validate_allowed_lifecycle(allowed_lifecycle):
    if isinstance(allowed_lifecycle, str):
        raise ValueError("allowed_lifecycle 必须为生命周期字符串序列")
    try:
        states = tuple(allowed_lifecycle)
    except TypeError:
        raise ValueError("allowed_lifecycle 必须为生命周期字符串序列")
    unknown = [state for state in states if state not in LIFECYCLE_STATES]
    if unknown:
        raise ValueError("allowed_lifecycle 含非法状态：%s" % unknown)
    if "retired" in states:
        raise ValueError("retired 不允许进入调度生命周期")
    return frozenset(states)


def _result(strategy_id, status, started, verdict=None, error=None):
    return {
        "strategy_id": strategy_id,
        "status": status,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "verdict": verdict,
        "error": error,
    }


def _timeout_for(strategy_id, default_timeout_s, strategy_timeouts):
    if strategy_timeouts is not None and strategy_id in strategy_timeouts:
        return strategy_timeouts[strategy_id]
    return default_timeout_s


def _run_one(registry, strategy_id, context, timeout_s, replay):
    """执行一项并把意外的调度边界异常收口为统一结果。"""
    started = time.perf_counter()
    try:
        result = run_strategy(
            registry,
            strategy_id,
            context,
            timeout_s=timeout_s,
            allow_deprecated=replay,
        )
    except Exception as exc:
        return _result(strategy_id, "error", started, error=str(exc))
    return result


class MultiStrategyScheduler:
    """可复用的多策略并行调度器。"""

    def __init__(self, registry, max_workers=None, timeout_s=None,
                 overall_timeout_s=None, allowed_lifecycle=None,
                 replay=False):
        self.registry = registry
        self.max_workers = max_workers
        self.timeout_s = timeout_s
        self.overall_timeout_s = overall_timeout_s
        self.allowed_lifecycle = allowed_lifecycle
        self.replay = replay
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers 必须为正整数")
        if allowed_lifecycle is not None:
            _validate_allowed_lifecycle(allowed_lifecycle)

    def _allowed_states(self, allowed_lifecycle, replay):
        explicit = allowed_lifecycle is not None or self.allowed_lifecycle is not None
        configured = (self.allowed_lifecycle if allowed_lifecycle is None
                      else allowed_lifecycle)
        states = set(_validate_allowed_lifecycle(
            _DEFAULT_ALLOWED_LIFECYCLE if configured is None else configured))
        if _REPLAY_LIFECYCLE in states and not replay:
            states.remove(_REPLAY_LIFECYCLE)
        if replay and not explicit:
            states.add(_REPLAY_LIFECYCLE)
        return states

    def run(self, strategy_ids, context, timeout_s=None,
            overall_timeout_s=None, max_workers=None,
            allowed_lifecycle=None, replay=None, strategy_timeouts=None):
        """并行运行 strategy_ids，返回按输入顺序排列的统一结果列表。

        ``timeout_s`` 是单策略默认超时；``strategy_timeouts`` 可按 strategy_id
        覆盖它。``overall_timeout_s`` 是整个调度批次的截止时间。超时后不等待
        后台线程，线程无法安全强杀这一事实由单策略结果明确暴露。
        ``deprecated`` 只有 replay=True 且未被显式生命周期配置排除时可运行。
        """
        strategy_ids = list(strategy_ids)
        if timeout_s is None:
            timeout_s = self.timeout_s
        if isinstance(timeout_s, Mapping):
            if strategy_timeouts is not None:
                raise ValueError("timeout_s 映射与 strategy_timeouts 不能同时提供")
            strategy_timeouts = timeout_s
            timeout_s = None
        if overall_timeout_s is None:
            overall_timeout_s = self.overall_timeout_s
        if max_workers is None:
            max_workers = self.max_workers
        if replay is None:
            replay = self.replay
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers 必须为正整数")
        if strategy_timeouts is not None and not isinstance(strategy_timeouts, Mapping):
            raise ValueError("strategy_timeouts 必须为 strategy_id 到秒数的映射")
        allowed_states = self._allowed_states(allowed_lifecycle, replay)
        started = time.perf_counter()
        results = [None] * len(strategy_ids)
        pending = []

        for index, strategy_id in enumerate(strategy_ids):
            try:
                agent = self.registry.get(strategy_id)
            except Exception as exc:
                results[index] = _result(
                    strategy_id, "error", started, error=str(exc))
                continue
            lifecycle_state = getattr(agent, "lifecycle_state", None)
            if lifecycle_state not in allowed_states:
                results[index] = _result(
                    strategy_id,
                    "rejected",
                    started,
                    error="strategy_id=%s lifecycle_state=%s 拒绝调度"
                          % (strategy_id, lifecycle_state),
                )
                continue
            pending.append((index, strategy_id))

        if not pending:
            return results

        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="strategy-scheduler",
        )
        futures = {}
        try:
            for index, strategy_id in pending:
                future = executor.submit(
                    _run_one,
                    self.registry,
                    strategy_id,
                    context,
                    _timeout_for(strategy_id, timeout_s, strategy_timeouts),
                    replay,
                )
                futures[future] = (index, strategy_id)
            remaining = overall_timeout_s
            if remaining is not None:
                remaining = max(0.0, remaining - (time.perf_counter() - started))
            done, not_done = wait(futures, timeout=remaining)
            for future in done:
                index, strategy_id = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    results[index] = _result(
                        strategy_id, "error", started, error=str(exc))
            for future in not_done:
                index, strategy_id = futures[future]
                future.cancel()
                results[index] = _result(
                    strategy_id,
                    "timeout",
                    started,
                    error="strategy_id=%s 超过 overall timeout" % strategy_id,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return results


def run_strategies(registry, strategy_ids, context, timeout_s=None,
                   overall_timeout_s=None, max_workers=None,
                   allowed_lifecycle=None, replay=False,
                   strategy_timeouts=None):
    """函数式调度入口。"""
    return MultiStrategyScheduler(
        registry,
        max_workers=max_workers,
        timeout_s=timeout_s,
        overall_timeout_s=overall_timeout_s,
        allowed_lifecycle=allowed_lifecycle,
        replay=replay,
    ).run(
        strategy_ids,
        context,
        strategy_timeouts=strategy_timeouts,
    )


schedule_strategies = run_strategies


__all__ = ["MultiStrategyScheduler", "run_strategies", "schedule_strategies"]
