# -*- coding: utf-8 -*-
"""策略 Agent 最小运行时隔离门。

本模块只负责调用已注册策略并返回内存中的运行结果：不写策略账本，不接交易执行。
运行前检查生命周期，并把 context 转换为递归只读结构；超时后显式返回，后台线程
无法被 Python 安全强杀这一事实不被隐藏。

超时包裹机制口径（2026-10-18 拍板 01，与 run_shadow._propose_with_timeout
docstring 裁决同源）：裸 daemon Thread + 结果盒，不用 ThreadPoolExecutor——
其 worker 线程 3.9+ 为非 daemon 且解释器退出时会 join 全部 worker，死循环
策略会把整个进程挂在 exit 上，与隔离门目标自相矛盾。
"""

from collections.abc import Mapping
from types import MappingProxyType
import threading
import time

from strategy_registry import StrategyError


_ALLOWED_LIFECYCLE = frozenset(("experimental", "active"))
_REPLAY_LIFECYCLE = "deprecated"
_DEFAULT_TIMEOUT_S = 10.0


def readonly_context(value):
    """返回 context 的递归只读快照。"""
    return _readonly(value, {})


def _readonly(value, memo):
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in memo:
            raise StrategyError("context 不支持循环引用")
        memo[marker] = True
        result = {
            _readonly(key, memo): _readonly(item, memo) for key, item in value.items()
        }
        del memo[marker]
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in memo:
            raise StrategyError("context 不支持循环引用")
        memo[marker] = True
        result = tuple(_readonly(item, memo) for item in value)
        del memo[marker]
        return result
    if isinstance(value, (set, frozenset)):
        marker = id(value)
        if marker in memo:
            raise StrategyError("context 不支持循环引用")
        memo[marker] = True
        result = frozenset(_readonly(item, memo) for item in value)
        del memo[marker]
        return result
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _result(strategy_id, status, started, verdict=None, error=None):
    return {
        "strategy_id": strategy_id,
        "status": status,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "verdict": verdict,
        "error": error,
    }


def run_strategy(
    registry, strategy_id, context, timeout_s=None, allow_deprecated=False
):
    """通过隔离门运行一次策略。

    ``allow_deprecated`` 是回放调用方必须显式传入的开关，仅放行 deprecated；
    retired 永不进入运行路径。超时返回后线程可能仍在后台运行，结果对此 fail-visible。
    """
    started = time.perf_counter()
    try:
        agent = registry.get(strategy_id)
    except Exception as exc:  # 注册表边界错误也必须返回可观察结果
        return _result(strategy_id, "error", started, error=str(exc))

    lifecycle_state = getattr(agent, "lifecycle_state", None)
    if lifecycle_state not in _ALLOWED_LIFECYCLE:
        if not (allow_deprecated and lifecycle_state == _REPLAY_LIFECYCLE):
            return _result(
                strategy_id,
                "rejected",
                started,
                error="strategy_id=%s lifecycle_state=%s 拒绝运行"
                % (strategy_id, lifecycle_state),
            )

    try:
        readonly = readonly_context(context)
    except Exception as exc:
        return _result(strategy_id, "error", started, error=str(exc))

    if timeout_s is None:
        timeout_s = _DEFAULT_TIMEOUT_S
    box = {}

    def _work():
        try:
            box["verdict"] = registry.run(strategy_id, readonly)
        except BaseException as e:  # noqa: BLE001  单策略异常 fail-visible，见 docstring
            box["error"] = e

    t = threading.Thread(
        target=_work, name="strategy-runtime-%s" % strategy_id, daemon=True
    )
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return _result(
            strategy_id,
            "timeout",
            started,
            error="strategy_id=%s 执行超时（%.3fs；后台线程无法强杀）"
            % (strategy_id, timeout_s),
        )
    if "error" in box:
        return _result(strategy_id, "error", started, error=str(box["error"]))
    return _result(strategy_id, "success", started, verdict=box.get("verdict"))


class StrategyRuntime:
    """可复用的最小策略运行时门。"""

    def __init__(self, registry, timeout_s=None):
        self.registry = registry
        self.timeout_s = timeout_s

    def run(self, strategy_id, context, allow_deprecated=False, timeout_s=None):
        return run_strategy(
            self.registry,
            strategy_id,
            context,
            self.timeout_s if timeout_s is None else timeout_s,
            allow_deprecated=allow_deprecated,
        )


__all__ = ["StrategyRuntime", "readonly_context", "run_strategy"]
