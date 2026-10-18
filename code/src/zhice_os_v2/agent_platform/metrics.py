"""Prometheus 指标（架构稿 §9-4）：周期耗时 / 仲裁路径分布 / LLM 延迟 / 闭环转化。

对齐 jev_common metrics 模式：模块级单例 Counter/Histogram，进程内注册一次。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:  # prometheus_client 为可选依赖（缺位时指标静默降级，不阻塞主链）
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        start_http_server,
    )
    _HAS_PROM = True
except Exception:  # pragma: no cover
    _HAS_PROM = False

# ── 指标定义（懒注册，避免重复注册异常） ───────────────────────────────────
_cycle_seconds: Histogram | None = None
_path_total: Counter | None = None
_llm_latency: Histogram | None = None
_loop_conversion_total: Counter | None = None     # T05: ack 处理计数
_decisions_total: Counter | None = None           # T05: 决策产出计数
_graduation_weight: Gauge | None = None           # T05: 毕业权重现值

# T05 端口约定：metrics 暴露端口读 AW_METRICS_PORT。
# 注意 9101 已被 jev_gate（C:\aios\jev-eval）占用、9109 为本模块历史缺省——
# 与 agent_platform config.metrics_port(9109) 冲突；T05 统一约定 9106（README 注明）。
DEFAULT_METRICS_PORT = 9106


def _ensure_metrics() -> None:
    global _cycle_seconds, _path_total, _llm_latency
    global _loop_conversion_total, _decisions_total, _graduation_weight
    if not _HAS_PROM or _cycle_seconds is not None:
        return
    _cycle_seconds = Histogram(
        "aw_cycle_seconds", "盯盘周期耗时(秒)",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    _path_total = Counter(
        "aw_arbitration_path_total", "仲裁路径分布", ["path"],
    )
    _llm_latency = Histogram(
        "aw_llm_latency_seconds", "LLM 仲裁延迟(秒)",
        buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 8.0),
    )
    # T05：旧 Gauge aw_loop_conversion{kind}（确认率/毕业数）从未有挂点消费，
    # 且与 Counter aw_loop_conversion_total{result} 在 prometheus_client 归一化
    # 后样本同名冲突（Gauge 样本名无后缀、Counter 样本名 _total 被剥）→ 移除旧
    # Gauge，闭环转化计数统一走 Counter aw_loop_conversion_total{result}。
    _loop_conversion_total = Counter(
        "aw_loop_conversion_total", "ack 处理计数(闭环转化)", ["result"],
    )
    _decisions_total = Counter(
        "aw_decisions_total", "决策产出计数", ["action"],
    )
    _graduation_weight = Gauge(
        "aw_graduation_weight", "毕业权重现值", ["method"],
    )


def observe_cycle(seconds: float) -> None:
    _ensure_metrics()
    if _cycle_seconds is not None:
        _cycle_seconds.observe(seconds)


def inc_path(path: str) -> None:
    _ensure_metrics()
    if _path_total is not None:
        _path_total.labels(path=path).inc()


def observe_llm_latency(seconds: float) -> None:
    _ensure_metrics()
    if _llm_latency is not None:
        _llm_latency.observe(seconds)


# ── T05 闭环指标：ack 转化 / 决策产出 / 毕业权重 ────────────────────────────
def inc_loop_conversion(result: str) -> None:
    """ack 处理计数；result ∈ {confirmed, ignored, expired}。"""
    _ensure_metrics()
    if _loop_conversion_total is not None:
        _loop_conversion_total.labels(result=result).inc()


def inc_decision(action: str) -> None:
    """决策产出计数；action ∈ {SELL, HOLD, WAIT}（不带 symbol，维度克制）。"""
    _ensure_metrics()
    if _decisions_total is not None:
        _decisions_total.labels(action=action).inc()


def set_graduation_weight(method: str, weight: float) -> None:
    """毕业权重现值 Gauge（apply_weights 后 set）。"""
    _ensure_metrics()
    if _graduation_weight is not None:
        _graduation_weight.labels(method=method).set(weight)


def start_metrics_server(port: int | None = None) -> bool:
    """暴露 /metrics；端口缺省读 AW_METRICS_PORT（默认 9106，9101 已被
    jev_gate 占用、9109 为 config 旧缺省——统一收敛到 9106，README 注明）。
    端口占用时降级告警不阻塞。"""
    if port is None:
        import os

        port = int(os.environ.get("AW_METRICS_PORT", DEFAULT_METRICS_PORT))
    if not _HAS_PROM:
        logger.warning("prometheus_client 缺位，指标不暴露")
        return False
    _ensure_metrics()
    try:
        start_http_server(port)
        logger.info("agent_platform metrics on :%d/metrics", port)
        return True
    except OSError as exc:
        logger.warning("metrics 端口 %d 占用: %s", port, exc)
        return False
