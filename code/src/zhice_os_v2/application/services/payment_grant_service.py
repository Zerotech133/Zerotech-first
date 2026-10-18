"""支付发放服务（C-3）：订单级幂等原子发放，按 product_type 只发对应权益。

铁律：
- 幂等来源 = order_id（PaymentRepository.apply_grant_atomic 单事务锁 order 行）；
- 支付**永不改角色**——只入账会员/订阅权益；
- permanent 权益收月费单标记 granted 但不降级；expired/revoked 续费从 now 起算清撤销、
  有效定期从原到期延长（与 B1 客服补单 AdminEntitlementService._extend_base 同一叠加语义）；
- inactive 用户仍履约（权益入账，账号能否登录由 auth 另行决定）；
- 历史归属歧义订单不自动发放，须人工复核（apply_grant_atomic 内拦截）。

分层：本模块 import PaymentRepository / membership ORM 映射（infrastructure）——同
payment_service 的既有范式（已入 test_architecture KNOWN_VIOLATIONS）。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from zhice_os_v2.application.services import alert_service
from zhice_os_v2.domain.billing.entitlement import (
    _as_aware,
    membership_effective_status,
    subscription_effective_status,
)
from zhice_os_v2.domain.billing.payment import PaymentOrder
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.repositories.membership_repository import (
    _to_membership,
    _to_subscription,
)
from zhice_os_v2.infrastructure.repositories.payment_repository import PaymentRepository

logger = logging.getLogger(__name__)

# 自动重试退避（分钟）：第 K 次自动尝试失败后，等 BACKOFF_MINUTES[K-1] 再排下次重试。
# 共 8 次自动尝试（MAX_AUTO_ATTEMPTS）；第 8 次仍失败 → grant_status=failed 终态 + 告警管理员。
BACKOFF_MINUTES = (1, 2, 5, 10, 30, 60, 180, 360)
MAX_AUTO_ATTEMPTS = len(BACKOFF_MINUTES)


class GrantAdminError(Exception):
    """C-5 管理员支付操作错误（billing.* 码 → 路由映射 HTTP）。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _extend_base(
    valid_until: datetime | None,
    is_permanent: bool,
    revoked_at: datetime | None,
    now: datetime,
) -> datetime:
    """月度叠加基准：有效定期 → max(now, 旧到期)；否则 now（含 expired/revoked/缺失）。

    与 AdminEntitlementService._extend_base 同语义，保证支付发放与客服补单口径一致。
    """
    if valid_until is not None and not is_permanent and revoked_at is None:
        vu = _as_aware(valid_until)
        if vu > now:
            return vu
    return now


class PaymentGrantService:
    def __init__(self, *, repo: PaymentRepository | None = None) -> None:
        self._repo = repo or PaymentRepository()

    def grant_order(self, order: PaymentOrder) -> str:
        """幂等发放一个已支付订单的权益。失败置 retrying 不抛（webhook 仍可回 ok）。

        返回 apply_grant_atomic 的状态串（granted/already_granted/not_found/bad_state/
        manual_review_required），或发放异常时 'retrying'。
        """
        try:
            return self._repo.apply_grant_atomic(
                order_id=order.order_id,
                lease_owner="inline",
                grant_fn=self._grant_in_session,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "支付发放失败 order=%s user=%s product=%s",
                order.order_id, order.user_id, order.product_type,
            )
            self._repo.mark_grant_retrying(
                order_id=order.order_id,
                error_code=type(e).__name__,
                error_summary=str(e),
            )
            return "retrying"

    def process_due_grants(
        self, *, lease_owner: str, now: datetime | None = None
    ) -> dict:
        """发放 worker（C-4）：认领所有 due 的 paid 订单逐个发放，失败退避重试。

        循环 ``claim_next_grant``（FOR UPDATE SKIP LOCKED，认领即置 processing+租约并递增
        自动尝试计数）→ ``apply_grant_atomic`` 发权益。发放异常 best-effort 记 retrying 并
        按 ``BACKOFF_MINUTES`` 排下次重试；达 ``MAX_AUTO_ATTEMPTS`` → failed 终态 + 告警。
        单单失败不阻断其余。返回本轮统计（claimed/granted/retrying/failed）。
        """
        now = now or utc_now()
        stats = {"claimed": 0, "granted": 0, "retrying": 0, "failed": 0}
        while True:
            order = self._repo.claim_next_grant(now=now, lease_owner=lease_owner)
            if order is None:
                break
            stats["claimed"] += 1
            try:
                status = self._repo.apply_grant_atomic(
                    order_id=order.order_id,
                    lease_owner=lease_owner,
                    grant_fn=self._grant_in_session,
                )
            except Exception as e:  # noqa: BLE001 — 单单失败隔离，退避重试
                self._handle_grant_failure(order, e, now, stats)
                continue
            if status in ("granted", "already_granted"):
                stats["granted"] += 1
            else:
                # claim 已过滤 ambiguous/非 paid，理论到不了 not_found/bad_state/
                # manual_review_required；真发生则记账并让下轮/人工兜底，不静默丢。
                logger.warning(
                    "支付发放认领后返回异常状态 order=%s status=%s",
                    order.order_id, status,
                )
                self._repo.mark_grant_failure(
                    order_id=order.order_id, lease_owner=lease_owner,
                    error_code="unexpected_status", error_summary=status,
                    next_retry_at=now, reached_max=False,
                )
                stats["retrying"] += 1
        return stats

    def _handle_grant_failure(
        self, order: PaymentOrder, exc: Exception, now: datetime, stats: dict
    ) -> None:
        """认领后发放抛错：按自动尝试次数退避 → retrying，或达上限 → failed + 告警。"""
        attempts = order.automatic_grant_attempts  # claim 已 +1
        reached_max = attempts >= MAX_AUTO_ATTEMPTS
        next_retry = (
            None if reached_max else now + timedelta(minutes=BACKOFF_MINUTES[attempts - 1])
        )
        logger.exception(
            "支付发放失败 order=%s user=%s attempt=%s reached_max=%s",
            order.order_id, order.user_id, attempts, reached_max,
        )
        self._repo.mark_grant_failure(
            order_id=order.order_id,
            lease_owner=order.grant_lease_owner or "",
            error_code=type(exc).__name__,
            error_summary=str(exc),
            next_retry_at=next_retry,
            reached_max=reached_max,
        )
        if reached_max:
            stats["failed"] += 1
            alert_service.send_alert(
                "critical",
                f"支付发放失败达上限 order={order.order_id}",
                f"user={order.user_id} product={order.product_type} "
                f"attempts={attempts} last_error={type(exc).__name__}: {exc}",
                dedup_open=True,
            )
        else:
            stats["retrying"] += 1

    # ── C-5 管理员支付发放管理（list / retry / review） ────────────
    def list_payment_grants(
        self,
        *,
        user_id: str | None = None,
        grant_status: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """支付发放待办分页（默认 paid 未 granted，可筛 user_id/grant_status/时间）。"""
        rows, total = self._repo.list_grant_orders(
            user_id=user_id, grant_status=grant_status,
            from_at=from_at, to_at=to_at, page=page, page_size=page_size,
        )
        return {
            "items": [_grant_order_view(o) for o in rows],
            "total": total, "page": page, "page_size": page_size,
        }

    def retry_grant(
        self, *, order_id: str, actor_user_id: str, reason: str,
        now: datetime | None = None,
    ) -> dict:
        """管理员手动重试发放（状态门 + 复用订单级幂等 grant_order）。

        granted → 返当前不重发；历史歧义 → 须走 review（grant_review_required）；
        processing 且租约未过期 → grant_in_progress（活 worker 持锁）；否则即时发放。
        """
        now = now or utc_now()
        order = self._repo.get_by_order_id(order_id)
        if order is None:
            raise GrantAdminError("billing.order_not_found")
        if order.grant_status == "granted":
            return {"status": "already_granted", "order": _grant_order_view(order)}
        if order.historical_grant_ambiguous:
            raise GrantAdminError("billing.grant_review_required")
        if order.grant_status == "processing" and order.grant_lease_until is not None:
            if _as_aware(order.grant_lease_until) > now:
                raise GrantAdminError("billing.grant_in_progress")
        logger.info(
            "管理员手动重试发放 order=%s actor=%s reason=%s",
            order_id, actor_user_id, reason,
        )
        status = self.grant_order(order)
        refreshed = self._repo.get_by_order_id(order_id) or order
        return {"status": status, "order": _grant_order_view(refreshed)}

    def review_grant(
        self, *, order_id: str, actor_user_id: str, decision: str, reason: str,
    ) -> dict:
        """历史歧义订单复核（仅 manual_review + ambiguous 可复核；写审计）。

        decision=RETRY 转 pending 交回 worker；MARK_GRANTED 对账标记 granted（不发权益）。
        """
        order = self._repo.get_by_order_id(order_id)
        if order is None:
            raise GrantAdminError("billing.order_not_found")
        if not order.historical_grant_ambiguous:
            raise GrantAdminError("billing.not_reviewable")
        updated = self._repo.review_ambiguous_grant(
            order_id=order_id, reviewer=actor_user_id,
            decision=decision, reason=reason,
        )
        if updated is None:  # 复核前订单被删（竞态）
            raise GrantAdminError("billing.order_not_found")
        return {"status": updated.grant_status, "order": _grant_order_view(updated)}

    # ── 单 session 发放（apply_grant_atomic 回调；同事务） ────────────
    def _grant_in_session(
        self, s: Session, order: PaymentOrder, now: datetime
    ) -> None:
        # 先锁定 user 行，与管理员权益写（_lock_user: user → membership/subscription）
        # 保持一致的全局锁序。否则本路径写权益审计（FK target_user_id → v2_user）会在
        # 已持 membership 锁后回锁 user，与管理员「持 user 锁等 membership」形成 AB-BA
        # 死锁。统一 user→membership 顺序后并发写串行化收敛，不再互锁。
        s.query(orm.UserModel).filter_by(
            user_id=order.user_id
        ).with_for_update().one_or_none()
        if order.product_type == "membership":
            self._grant_membership(s, order, now)
        else:
            self._grant_subscription(s, order, now)

    def _grant_membership(
        self, s: Session, order: PaymentOrder, now: datetime
    ) -> None:
        row = (
            s.query(orm.MembershipModel)
            .filter_by(user_id=order.user_id)
            .with_for_update()
            .one_or_none()
        )
        before = _membership_snapshot(row, now)
        # permanent 未撤销 → 收月费 no-op 不降级（订单仍由外层标 granted）。
        if row is not None and row.is_permanent and row.revoked_at is None:
            after = before
        else:
            base = _extend_base(
                None if row is None else row.valid_until,
                False if row is None else row.is_permanent,
                None if row is None else row.revoked_at,
                now,
            )
            new_valid_until = base + timedelta(days=30 * order.months)
            if row is None:
                row = orm.MembershipModel(
                    user_id=order.user_id, tier="member", status="active",
                    version=1, created_at=now, updated_at=now,
                )
                s.add(row)
            else:
                row.tier = "member"
                row.status = "active"
                row.version += 1
                row.updated_at = now
            row.is_permanent = False
            row.revoked_at = None
            row.revoked_by_user_id = None
            row.valid_until = new_valid_until
            s.flush()
            after = _membership_snapshot(row, now)
        _write_payment_audit(
            s, order, now, resource_type="MEMBERSHIP", resource_id=None,
            before=before, after=after,
        )

    def _grant_subscription(
        self, s: Session, order: PaymentOrder, now: datetime
    ) -> None:
        strategy_id = order.product_ref
        row = (
            s.query(orm.StrategySubscriptionModel)
            .filter_by(user_id=order.user_id, strategy_id=strategy_id)
            .with_for_update()
            .one_or_none()
        )
        before = _sub_snapshot(row, now)
        if row is not None and row.is_permanent and row.revoked_at is None:
            after = before
        else:
            base = _extend_base(
                None if row is None else row.valid_until,
                False if row is None else row.is_permanent,
                None if row is None else row.revoked_at,
                now,
            )
            new_valid_until = base + timedelta(days=30 * order.months)
            if row is None:
                row = orm.StrategySubscriptionModel(
                    user_id=order.user_id, strategy_id=strategy_id, status="active",
                    version=1, created_at=now, updated_at=now,
                )
                s.add(row)
            else:
                row.status = "active"
                row.version += 1
                row.updated_at = now
            row.is_permanent = False
            row.revoked_at = None
            row.revoked_by_user_id = None
            row.valid_until = new_valid_until
            s.flush()
            after = _sub_snapshot(row, now)
        _write_payment_audit(
            s, order, now, resource_type="SUBSCRIPTION", resource_id=strategy_id,
            before=before, after=after,
        )


def _iso(dt: datetime | None) -> str | None:
    return _as_aware(dt).isoformat() if dt is not None else None


def _grant_order_view(o: PaymentOrder) -> dict:
    """支付发放订单的管理视图（含发放状态机全字段；金额→str、时间→ISO）。"""
    return {
        "order_id": o.order_id,
        "user_id": o.user_id,
        "product_type": o.product_type,
        "product_ref": o.product_ref,
        "months": o.months,
        "amount_usdt": str(o.amount_usdt),
        "status": o.status,
        "grant_status": o.grant_status,
        "grant_attempts": o.grant_attempts,
        "automatic_grant_attempts": o.automatic_grant_attempts,
        "next_grant_retry_at": _iso(o.next_grant_retry_at),
        "last_grant_error_code": o.last_grant_error_code,
        "last_grant_error_summary": o.last_grant_error_summary,
        "grant_lease_owner": o.grant_lease_owner,
        "grant_lease_until": _iso(o.grant_lease_until),
        "historical_grant_ambiguous": o.historical_grant_ambiguous,
        "grant_reviewed_at": _iso(o.grant_reviewed_at),
        "grant_reviewed_by_user_id": o.grant_reviewed_by_user_id,
        "grant_review_decision": o.grant_review_decision,
        "granted_at": _iso(o.granted_at),
        "paid_at": _iso(o.paid_at),
        "product_name_snapshot": o.product_name_snapshot,
        "market_snapshot": o.market_snapshot,
    }


def _membership_snapshot(row: orm.MembershipModel | None, now: datetime) -> dict:
    if row is None:
        return {
            "effective_status": "free", "version": 0,
            "is_permanent": False, "valid_until": None,
        }
    m = _to_membership(row)
    return {
        "effective_status": membership_effective_status(m, now),
        "version": m.version,
        "is_permanent": m.is_permanent,
        "valid_until": m.valid_until.isoformat() if m.valid_until else None,
    }


def _sub_snapshot(row: orm.StrategySubscriptionModel | None, now: datetime) -> dict:
    if row is None:
        return {
            "effective_status": "expired", "version": 0,
            "is_permanent": False, "valid_until": None,
        }
    sub = _to_subscription(row)
    return {
        "strategy_id": sub.strategy_id,
        "effective_status": subscription_effective_status(sub, now),
        "version": sub.version,
        "is_permanent": sub.is_permanent,
        "valid_until": sub.valid_until.isoformat() if sub.valid_until else None,
    }


def _write_payment_audit(
    s: Session,
    order: PaymentOrder,
    now: datetime,
    *,
    resource_type: str,
    resource_id: str | None,
    before: dict,
    after: dict,
) -> None:
    """发放事务内写一条 PAYMENT 审计（source_order_id 唯一 → 每单一条，不含敏感数据）。"""
    s.add(
        orm.EntitlementAuditModel(
            audit_id=f"aud-{secrets.token_hex(12)}",
            operation_id=None,
            actor_user_id=None,
            target_user_id=order.user_id,
            source_type="PAYMENT",
            action="payment.grant",
            resource_type=resource_type,
            resource_id=resource_id,
            reason=f"payment order {order.order_id}",
            before_state=before,
            after_state=after,
            source_order_id=order.order_id,
            created_at=now,
        )
    )
