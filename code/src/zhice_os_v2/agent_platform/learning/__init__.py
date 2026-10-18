"""learning/ — 学习闭环（T04 已实装）：卡片送审 → ack → LearningCase → T+5 回填
→ 记忆沉淀 → 毕业判据 v2 → 权重更新 → 回到仲裁（架构稿 §5 八步全链）。

各模块职责（实现细节见各文件 docstring）：
- card_bridge.CardBridge   飞书卡片送审（webhook/OpenAPI/dryrun 三模式）+ 文件 ack 回收
- case_writer.CaseWriter   ack → aw_decision 状态 + zhice LearningCase + T+5 回填
- memory_sink.MemorySink   cowbridge daily 层异步沉淀（失败降级 JSONL）+ 关键词检索
- graduation.GraduationService  毕业判据 v2 + 权重热更新（闭环合拢点）

无飞书凭证时全程 dryrun 可演示：卡片 JSON 落盘 → 模拟 ack 文件 → aw 表更新
→ memory JSONL → graduate 权重变化（见 README T04 章节）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zhice_os_v2.agent_platform.config import utc_now


@dataclass
class CardAck:
    """人审回执（token:action:decision_id 去重）。"""

    decision_id: str
    operator: str
    action: str                 # CONFIRM_SELL / IGNORE
    reason: str = ""
    acked_at: str = field(default_factory=lambda: utc_now().isoformat())


# 实装类自子模块导入（保持 `from ...learning import CardBridge` 的既有引用不变）
from zhice_os_v2.agent_platform.learning.card_bridge import (   # noqa: E402
    CardBridge,
)
from zhice_os_v2.agent_platform.learning.case_writer import (   # noqa: E402
    CaseWriter,
)
from zhice_os_v2.agent_platform.learning.graduation import (    # noqa: E402
    GraduationService,
)
from zhice_os_v2.agent_platform.learning.memory_sink import (   # noqa: E402
    MemorySink,
)

__all__ = [
    "CardAck",
    "CardBridge",
    "CaseWriter",
    "MemorySink",
    "GraduationService",
]
