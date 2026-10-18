"""管理员全局访问审计领域对象。

只承载可持久化的元数据：actor_user_id/action/resource_type/resource_id/request_id/
created_at。**绝不**承载密码/Token/敏感响应正文——to_dict() 仅暴露上述字段，是"不存
敏感正文"策略的显式载体（单测 test_audit_does_not_store_sensitive_body 固化）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AccessAudit:
    audit_id: str
    actor_user_id: str
    action: str
    resource_type: str
    request_id: str
    created_at: datetime
    resource_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "audit_id": self.audit_id,
            "actor_user_id": self.actor_user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "request_id": self.request_id,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }
