"""盯盘 Agent 中台（agent_platform）— 零侵入新包，对 zhice 主干只读。

架构依据：C:\\项目\\deliverables\\agent-watch-platform-architecture-20261016.md
分层：方法插件(methods/) → 三步仲裁(arbitration/) → 运行时(runtime/)
      → 学习闭环(learning/, v1 仅 stub) → 回测引擎(backtest/, v1 核心交付)。

工程约定（§8）：
- 表前缀 aw_，与 zhice v2_* 物理隔离；aw_* 表用 create_all 自建，不写 alembic。
- 对 zhice 主干只 import / 只调用 / 只查询。
- LLM 调用只经 LlmArbiter 令牌桶（660 判定/h），不得绕过直连 Ollama。
- 决策全落 aw_decision 留痕（votes_json / model_verdict / arbitration_path /
  status / fwd_ret_5d 永不删改）。
- 时间口径全部 UTC。
"""
from __future__ import annotations

__version__ = "0.1.0"

AGENT_PLATFORM_TABLE_PREFIX = "aw_"


def __getattr__(name: str):  # PEP 562 惰性导出，避免 import 期拉起 pandas/sqlalchemy
    if name == "AgentPlatformConfig":
        from zhice_os_v2.agent_platform.config import AgentPlatformConfig

        return AgentPlatformConfig
    if name == "CapacityGuard":
        from zhice_os_v2.agent_platform.capacity import CapacityGuard

        return CapacityGuard
    if name == "MethodRegistry":
        from zhice_os_v2.agent_platform.methods import MethodRegistry

        return MethodRegistry
    if name == "RegimeArbiter":
        from zhice_os_v2.agent_platform.arbitration.regime_arbiter import RegimeArbiter

        return RegimeArbiter
    if name == "BacktestEngine":
        from zhice_os_v2.agent_platform.backtest.engine import BacktestEngine

        return BacktestEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
