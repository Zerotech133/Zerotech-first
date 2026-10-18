"""AgentPlatformConfig — 分片 / 节奏 / 水位 / 模型 / 仲裁阈值统一配置。

容量与仲裁公式的全部系数集中在此（架构稿 §3.2 / §4.2），改参数不改公式。
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """全包统一 UTC 时钟（对齐 zhice 时间口径约定）。"""
    return datetime.now(UTC)


def _default_report_dir() -> str:
    """报告默认落 code/agent_platform_reports/（包在 src/zhice_os_v2/ 下，回退 3 级）。"""
    pkg_dir = Path(__file__).resolve()
    for parent in pkg_dir.parents:
        if parent.name == "src":
            return str(parent.parent / "agent_platform_reports")
    return str(pkg_dir.parent.parent.parent / "agent_platform_reports")


def _default_db_url() -> str:
    """复用 zhice settings 的 db_main_url（只读 import，不复制凭据）。

    settings 不可用时回退环境变量，再回退 zhice-db 容器默认 DSN。
    """
    try:
        from zhice_os_v2.config.settings import get_settings

        return get_settings().db_main_url
    except Exception:  # pragma: no cover - 独立部署兜底
        import os

        return os.environ.get(
            "ZHICE_DB_URL", "postgresql://zhice:zhice_pass@localhost:5446/zhice_os_v2"
        )


class LlmArbiterConfig(BaseModel):
    """LLM 兜底仲裁配置（Ollama REST + 令牌桶）。"""

    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout_s: float = 5.0                       # 超时 → 保守回退 WAIT（path=DROPPED）
    bucket_per_hour: int = 660                   # 全平台共享判定预算（50% 水位）
    min_conf: float = 0.5                        # 低置信 → 保守 WAIT（path=LLM，verdict 留痕）
    position_value_threshold: float = 10000.0    # SELL 且持仓额>阈值 → 高风险复核进 LLM


class ArbitrationConfig(BaseModel):
    """三步仲裁系数（架构稿 §4.2 硬规范）。"""

    tie_margin: float = 0.15          # |score(SELL)-score(HOLD)| < margin → 平票进 LLM
    min_quorum_votes: int = 2         # 法定人数：有效票（非 WAIT）≥2
    min_sum_conf: float = 1.0         # 且 Σconf ≥ 1.0，否则直接 WAIT
    risk_off_sell_mult: float = 1.5   # risk-off 时 SELL 票权重放大（顺大势）
    risk_off_hold_mult: float = 0.5   # risk-off 时 HOLD 票权重萎缩


class CapacityConfig(BaseModel):
    """容量公式系数（架构稿 §3.2）。"""

    c_eval_ms: float = 10.0   # 单次方法评估耗时（保守假设，可用 benchmark 实测回填）
    eta: float = 0.6          # CPU 安全水位（给 GC/ORM/pandas 留余量）
    period_ms: dict[str, float] = Field(
        default_factory=lambda: {
            "5s": 5000.0,    # CRYPTO 快照档
            "10s": 10000.0,  # CN 盘中档
            "3m": 180000.0,  # US/HK 档
        }
    )


class AgentPlatformConfig(BaseModel):
    """agent_platform 顶层配置。"""

    market: str = "CRYPTO"
    snapshot_hz: str = "5s"                 # 5s / 10s / 3m（对齐 zhice 调度节奏）
    db_url: str = Field(default_factory=_default_db_url)
    report_dir: str = Field(default_factory=_default_report_dir)
    metrics_port: int = 9109                # Prometheus 暴露端口
    initial_method_weight: float = 1.0      # 方法初始毕业权重
    capacity: CapacityConfig = Field(default_factory=CapacityConfig)
    arbitration: ArbitrationConfig = Field(default_factory=ArbitrationConfig)
    llm: LlmArbiterConfig = Field(default_factory=LlmArbiterConfig)

    # 回测默认档
    backtest_freq: str = "15m"              # 5m / 15m / 1h
    backtest_days: int = 30
    fwd_horizon_bars: int = 5               # T+5 前向收益（按当前 bar 档位计 5 根）
    warmup_bars: int = 60                   # 方法预热窗口（< 此数不投票）
    window_bars: int = 120                  # WatchContext 滚动窗口长度

    model_config: dict[str, Any] = {"extra": "ignore"}
