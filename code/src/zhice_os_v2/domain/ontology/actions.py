"""统一 Action 模型。

Palantir 思想：用户不直接改数据，而是执行 Action。
每个 Action 都包含目标对象、参数、状态、操作人、审批人、审计轨迹。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import ObjectRef


def utc_now() -> datetime:
    return datetime.now(UTC)


class ActionStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AuditEntry:
    action_id: str
    event: str
    actor: str | None
    occurred_at: datetime = field(default_factory=utc_now)
    detail: dict = field(default_factory=dict)


@dataclass
class Action:
    action_id: str
    action_type: str
    target: ObjectRef
    parameters: dict = field(default_factory=dict)
    status: ActionStatus = ActionStatus.DRAFT
    requested_by: str | None = None
    approved_by: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    decided_at: datetime | None = None
    audit_trail: list[AuditEntry] = field(default_factory=list)

    def submit(self, actor: str) -> None:
        self.status = ActionStatus.PENDING_APPROVAL
        self.requested_by = actor
        self.audit_trail.append(AuditEntry(self.action_id, "SUBMITTED", actor))

    def approve(self, actor: str) -> None:
        if self.status != ActionStatus.PENDING_APPROVAL:
            raise ValueError(f"cannot approve action in status {self.status}")
        self.status = ActionStatus.EXECUTING
        self.approved_by = actor
        self.decided_at = utc_now()
        self.audit_trail.append(AuditEntry(self.action_id, "APPROVED", actor))

    def reject(self, actor: str, reason: str) -> None:
        if self.status != ActionStatus.PENDING_APPROVAL:
            raise ValueError(f"cannot reject action in status {self.status}")
        self.status = ActionStatus.REJECTED
        self.decided_at = utc_now()
        self.audit_trail.append(
            AuditEntry(self.action_id, "REJECTED", actor, detail={"reason": reason})
        )

    def mark_succeeded(self, actor: str | None = None) -> None:
        self.status = ActionStatus.SUCCEEDED
        self.decided_at = utc_now()
        self.audit_trail.append(AuditEntry(self.action_id, "SUCCEEDED", actor))

    def mark_failed(self, actor: str | None, reason: str) -> None:
        self.status = ActionStatus.FAILED
        self.decided_at = utc_now()
        self.audit_trail.append(
            AuditEntry(self.action_id, "FAILED", actor, detail={"reason": reason})
        )
