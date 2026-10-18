"""Playbook（打法包）层 — T06：不同交易手法不同打法包，商业化 SKU 载体。

设计依据：C:\\项目\\deliverables\\org-style-playbook-v0.1.md（第二节注册规格）。
分层：spec.py（PlaybookSpec/加载校验）→ mapping.py（标的→包映射持久化）
      → runtime/watch_daemon 消费（分组执行）→ graduation 按 (method, playbook) 归因。
"""

from zhice_os_v2.agent_platform.playbooks.spec import (
    DEFAULT_PLAYBOOK_ID,
    KNOWN_METHOD_IDS,
    LEGACY_METHOD_IDS,
    RESERVED_METHOD_IDS,
    ArbitrationParams,
    MethodBinding,
    PlaybookSpec,
    default_playbook,
    load_playbook,
)

__all__ = [
    "DEFAULT_PLAYBOOK_ID",
    "KNOWN_METHOD_IDS",
    "LEGACY_METHOD_IDS",
    "RESERVED_METHOD_IDS",
    "ArbitrationParams",
    "MethodBinding",
    "PlaybookSpec",
    "load_playbook",
    "default_playbook",
]
