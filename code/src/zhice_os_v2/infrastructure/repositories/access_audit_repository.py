"""AccessAuditRepository: v2_access_audit 表的持久化（管理员全局访问审计）。

与 user_account_repository.py 同范式：每方法自开 session、try/finally close。审计是
append-only：record 只 insert，latest 供测试/巡检取最近一条（created_at desc、id desc）。
只落元数据，绝不写密码/Token/响应正文（由 AccessAudit 领域对象本身保证）。
"""
from __future__ import annotations

from zhice_os_v2.domain.security.access_audit import AccessAudit
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _to_domain(row: orm.AccessAuditModel) -> AccessAudit:
    return AccessAudit(
        audit_id=row.audit_id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        request_id=row.request_id,
        created_at=row.created_at,
    )


class AccessAuditRepository:
    def _s(self):
        return get_session()

    def record(self, audit: AccessAudit) -> AccessAudit:
        s = self._s()
        try:
            row = orm.AccessAuditModel(
                audit_id=audit.audit_id,
                actor_user_id=audit.actor_user_id,
                action=audit.action,
                resource_type=audit.resource_type,
                resource_id=audit.resource_id,
                request_id=audit.request_id,
                created_at=audit.created_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def latest(self) -> AccessAudit | None:
        s = self._s()
        try:
            row = (
                s.query(orm.AccessAuditModel)
                .order_by(
                    orm.AccessAuditModel.created_at.desc(),
                    orm.AccessAuditModel.id.desc(),
                )
                .first()
            )
            return _to_domain(row) if row is not None else None
        finally:
            s.close()
