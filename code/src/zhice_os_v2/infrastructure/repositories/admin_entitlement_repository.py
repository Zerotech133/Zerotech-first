"""AdminEntitlementRepository：v2_admin_operation / v2_entitlement_audit 读写。

B1-3 提供幂等查询与审计分页/最近读取；B1-6 服务在共享事务内调用 session 版写入。
同步 session 模式（同 user_repository.py）。ORM 行 → 领域 dataclass 出边界。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from sqlalchemy.orm import Session

from zhice_os_v2.domain.billing.admin_entitlement import (
    AdminOperation,
    AuditQuery,
    EntitlementAudit,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _to_operation(row: orm.AdminOperationModel) -> AdminOperation:
    return AdminOperation(
        operation_id=row.operation_id,
        actor_user_id=row.actor_user_id,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        action=row.action,
        target_user_id=row.target_user_id,
        response_status=row.response_status,
        response_body=row.response_body,
        created_at=row.created_at,
    )


def _to_audit(row: orm.EntitlementAuditModel) -> EntitlementAudit:
    return EntitlementAudit(
        audit_id=row.audit_id,
        operation_id=row.operation_id,
        actor_user_id=row.actor_user_id,
        target_user_id=row.target_user_id,
        # DB CHECK 约束保证取值合法；ORM 列静态类型为 str，向 Literal 收窄。
        source_type=cast('Literal["ADMIN", "PAYMENT"]', row.source_type),
        action=row.action,
        resource_type=cast(
            'Literal["ROLE", "MEMBERSHIP", "SUBSCRIPTION", "PAYMENT_GRANT"]',
            row.resource_type,
        ),
        resource_id=row.resource_id,
        reason=row.reason,
        before_state=row.before_state,
        after_state=row.after_state,
        source_order_id=row.source_order_id,
        created_at=row.created_at,
    )


class AdminEntitlementRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session

    def _s(self) -> Session:
        return self._session_factory()

    def get_operation(
        self, actor_user_id: str, idempotency_key: str
    ) -> AdminOperation | None:
        s = self._s()
        try:
            row = (
                s.query(orm.AdminOperationModel)
                .filter_by(actor_user_id=actor_user_id, idempotency_key=idempotency_key)
                .first()
            )
            return _to_operation(row) if row else None
        finally:
            s.close()

    def recent_audits(
        self, target_user_id: str, *, limit: int = 20
    ) -> list[EntitlementAudit]:
        s = self._s()
        try:
            rows = (
                s.query(orm.EntitlementAuditModel)
                .filter_by(target_user_id=target_user_id)
                .order_by(
                    orm.EntitlementAuditModel.created_at.desc(),
                    orm.EntitlementAuditModel.audit_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [_to_audit(r) for r in rows]
        finally:
            s.close()

    def list_audits(self, query: AuditQuery) -> tuple[list[EntitlementAudit], int]:
        s = self._s()
        try:
            q = s.query(orm.EntitlementAuditModel)
            if query.target_user_id is not None:
                q = q.filter(
                    orm.EntitlementAuditModel.target_user_id == query.target_user_id
                )
            if query.actor_user_id is not None:
                q = q.filter(
                    orm.EntitlementAuditModel.actor_user_id == query.actor_user_id
                )
            if query.action is not None:
                q = q.filter(orm.EntitlementAuditModel.action == query.action)
            if query.resource_type is not None:
                q = q.filter(
                    orm.EntitlementAuditModel.resource_type == query.resource_type
                )
            if query.strategy_id is not None:
                # strategy_id 映射到通用 resource_id（订阅审计以 strategy_id 为 resource_id）。
                q = q.filter(
                    orm.EntitlementAuditModel.resource_id == query.strategy_id
                )
            if query.from_at is not None:
                q = q.filter(
                    orm.EntitlementAuditModel.created_at >= query.from_at
                )
            if query.to_at is not None:
                q = q.filter(orm.EntitlementAuditModel.created_at <= query.to_at)

            total = q.count()
            page = max(1, query.page)
            page_size = max(1, query.page_size)
            rows = (
                q.order_by(
                    orm.EntitlementAuditModel.created_at.desc(),
                    orm.EntitlementAuditModel.audit_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return [_to_audit(r) for r in rows], total
        finally:
            s.close()

    def count_operations(self) -> int:
        s = self._s()
        try:
            return s.query(orm.AdminOperationModel).count()
        finally:
            s.close()

    def count_audits(self) -> int:
        s = self._s()
        try:
            return s.query(orm.EntitlementAuditModel).count()
        finally:
            s.close()
