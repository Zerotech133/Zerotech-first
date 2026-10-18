"""PaymentRepository：v2_payment_order / v2_payment_event 的 CRUD + 幂等处理。

同步 session 模式（同 user_repository.py）。ORM 行 → PaymentOrder dataclass 出边界。
幂等核心：process_payment 在单事务内“插 payment_event(unique trade_id) + 订单转 paid”，
重复回调命中唯一键 → 返回 'duplicate'，绝不重复发放。
"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from zhice_os_v2.domain.billing.payment import PaymentOrder
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _to_order(row: orm.PaymentOrderModel) -> PaymentOrder:
    return PaymentOrder(
        order_id=row.order_id,
        user_id=row.user_id,
        product_type=row.product_type,
        product_ref=row.product_ref,
        months=row.months,
        amount_usdt=row.amount_usdt,
        trade_type=row.trade_type,
        status=row.status,
        bepusdt_trade_id=row.bepusdt_trade_id,
        pay_address=row.pay_address,
        actual_amount=row.actual_amount,
        expired_at=row.expired_at,
        paid_at=row.paid_at,
        granted_at=row.granted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        grant_status=row.grant_status,
        grant_attempts=row.grant_attempts,
        automatic_grant_attempts=row.automatic_grant_attempts,
        next_grant_retry_at=row.next_grant_retry_at,
        last_grant_error_code=row.last_grant_error_code,
        last_grant_error_summary=row.last_grant_error_summary,
        grant_lease_until=row.grant_lease_until,
        grant_lease_owner=row.grant_lease_owner,
        historical_grant_ambiguous=row.historical_grant_ambiguous,
        grant_reviewed_at=row.grant_reviewed_at,
        grant_reviewed_by_user_id=row.grant_reviewed_by_user_id,
        grant_review_decision=row.grant_review_decision,
        product_name_snapshot=row.product_name_snapshot,
        market_snapshot=row.market_snapshot,
    )


class PaymentRepository:
    def _s(self):
        return get_session()

    def create_order(
        self,
        *,
        order_id: str,
        user_id: str,
        product_type: str,
        product_ref: str | None,
        months: int,
        amount_usdt: Decimal,
        trade_type: str,
    ) -> PaymentOrder:
        s = self._s()
        try:
            now = utc_now()
            row = orm.PaymentOrderModel(
                order_id=order_id,
                user_id=user_id,
                product_type=product_type,
                product_ref=product_ref,
                months=months,
                amount_usdt=amount_usdt,
                trade_type=trade_type,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _to_order(row)
        finally:
            s.close()

    def attach_gateway(
        self,
        order_id: str,
        *,
        trade_id: str | None,
        pay_address: str | None,
        expired_at: datetime | None,
    ) -> None:
        """回填 BEpusdt 下单返回（trade_id / 收款地址 / 过期时刻）。"""
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel)
                .filter_by(order_id=order_id)
                .first()
            )
            if row is not None:
                row.bepusdt_trade_id = trade_id
                row.pay_address = pay_address
                row.expired_at = expired_at
                row.updated_at = utc_now()
                s.commit()
        finally:
            s.close()

    def get_by_order_id(self, order_id: str) -> PaymentOrder | None:
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel).filter_by(order_id=order_id).first()
            )
            return _to_order(row) if row else None
        finally:
            s.close()

    def get_owned(self, order_id: str, user_id: str) -> PaymentOrder | None:
        o = self.get_by_order_id(order_id)
        return o if (o is not None and o.user_id == user_id) else None

    def list_by_user(self, user_id: str) -> list[PaymentOrder]:
        s = self._s()
        try:
            rows = (
                s.query(orm.PaymentOrderModel)
                .filter_by(user_id=user_id)
                .order_by(orm.PaymentOrderModel.created_at.desc())
                .all()
            )
            return [_to_order(r) for r in rows]
        finally:
            s.close()

    def process_payment(
        self,
        *,
        order_id: str,
        trade_id: str,
        actual_amount: Decimal,
        block_txid: str | None,
        raw: dict,
    ) -> str:
        """单事务幂等处理：插 payment_event(unique trade_id) + 订单 pending→paid。

        返回：
        - 'processed'  首次处理成功（调用方随后发放权益 + mark_granted）；
        - 'duplicate'  trade_id 已处理过（重复回调）；
        - 'not_found'  无此订单；
        - 'bad_state'  订单非 pending（已 paid/expired/canceled，不再转移）。
        绝不重复发放：唯一键冲突即 duplicate。
        """
        s = self._s()
        try:
            # 串行化点：锁订单行，防同一 order_id 收到两个不同 trade_id 的并发回调
            # 各自读到 pending 后双双置 paid → 上层重复发放权益（唯一键只挡同 trade_id）。
            row = (
                s.query(orm.PaymentOrderModel)
                .filter_by(order_id=order_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return "not_found"
            # 幂等去重：trade_id 已存在 → duplicate（先查，冲突再兜底 rollback）。
            exists = (
                s.query(orm.PaymentEventModel)
                .filter_by(trade_id=trade_id)
                .first()
            )
            if exists is not None:
                return "duplicate"
            if row.status != "pending":
                return "bad_state"
            now = utc_now()
            s.add(
                orm.PaymentEventModel(
                    order_id=order_id,
                    trade_id=trade_id,
                    block_txid=block_txid,
                    raw=raw,
                    created_at=now,
                )
            )
            row.status = "paid"
            row.actual_amount = actual_amount
            row.bepusdt_trade_id = row.bepusdt_trade_id or trade_id
            row.notify_raw = raw
            row.paid_at = now
            # 单元 C：webhook 事务只可靠置 paid + 发放待办（grant_status=pending），
            # 并把 next_grant_retry_at 钉到 paid_at，供发放 worker 立即认领（崩溃兜底）。
            row.grant_status = "pending"
            row.next_grant_retry_at = now
            row.updated_at = now
            s.commit()
            return "processed"
        except Exception:
            s.rollback()
            # 并发下唯一键冲突亦视为已处理（另一路已入库）。
            dup = self._s()
            try:
                hit = (
                    dup.query(orm.PaymentEventModel)
                    .filter_by(trade_id=trade_id)
                    .first()
                )
                return "duplicate" if hit is not None else "not_found"
            finally:
                dup.close()
        finally:
            s.close()

    def mark_granted(self, order_id: str) -> None:
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel).filter_by(order_id=order_id).first()
            )
            if row is not None:
                row.granted_at = utc_now()
                row.updated_at = utc_now()
                s.commit()
        finally:
            s.close()

    def apply_grant_atomic(
        self,
        *,
        order_id: str,
        lease_owner: str,
        grant_fn: Callable[[Session, PaymentOrder, datetime], None],
    ) -> str:
        """订单级原子发放（幂等来源=order_id）：单事务锁 order→按状态收敛→发权益→granted。

        流程：锁 order 行（串行化同订单并发发放）→ 已 granted 幂等返回 → 非 paid 拒绝 →
        历史歧义须人工复核（不自动发）→ 调 ``grant_fn(session, order, now)`` 在**同一
        session/事务**内发对应权益并写 PAYMENT 审计 → 置 grant_status=granted + granted_at +
        清租约 → commit。grant_fn 抛错则整体回滚（不留权益/审计、不置 granted）。

        返回：'granted' / 'already_granted' / 'not_found' / 'bad_state' /
        'manual_review_required'。``lease_owner`` 为发放者标识（C-4 worker 认领时写入行
        租约；即时发放传内联标识），成功发放清空租约归属。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel)
                .filter_by(order_id=order_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return "not_found"
            if row.grant_status == "granted":
                return "already_granted"
            if row.status != "paid":
                return "bad_state"
            if row.historical_grant_ambiguous:
                # 历史 paid 但发放归属不明：只能人工复核（C-5），绝不自动发放。
                return "manual_review_required"
            now = utc_now()
            order = _to_order(row)
            grant_fn(s, order, now)
            row.grant_status = "granted"
            row.granted_at = now
            row.grant_lease_until = None
            row.grant_lease_owner = None
            row.next_grant_retry_at = None
            row.last_grant_error_code = None
            row.last_grant_error_summary = None
            row.updated_at = now
            s.commit()
            return "granted"
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def claim_next_grant(
        self, *, now: datetime, lease_owner: str, lease_seconds: int = 300
    ) -> PaymentOrder | None:
        """认领一个待发放订单（FOR UPDATE SKIP LOCKED，PG 下多 worker 不重复领）。

        候选（均限 paid 且非历史歧义）：
        - due 的 pending/retrying（``next_grant_retry_at <= now``）；
        - 崩溃回收：processing 且租约过期（``grant_lease_until < now``）。
        认领即写 processing + 新租约（owner/until）并递增 grant_attempts、
        automatic_grant_attempts。无候选返回 None。SQLite 无 SKIP LOCKED（单线程测逻辑）。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel)
                .filter(
                    orm.PaymentOrderModel.status == "paid",
                    orm.PaymentOrderModel.historical_grant_ambiguous.is_(False),
                    orm.PaymentOrderModel.grant_status != "granted",
                    sa.or_(
                        sa.and_(
                            orm.PaymentOrderModel.grant_status.in_(
                                ("pending", "retrying")
                            ),
                            orm.PaymentOrderModel.next_grant_retry_at <= now,
                        ),
                        sa.and_(
                            orm.PaymentOrderModel.grant_status == "processing",
                            orm.PaymentOrderModel.grant_lease_until < now,
                        ),
                    ),
                )
                .order_by(orm.PaymentOrderModel.next_grant_retry_at.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if row is None:
                return None
            row.grant_status = "processing"
            row.grant_lease_owner = lease_owner
            row.grant_lease_until = now + timedelta(seconds=lease_seconds)
            row.grant_attempts += 1
            row.automatic_grant_attempts += 1
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return _to_order(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def mark_grant_failure(
        self,
        *,
        order_id: str,
        lease_owner: str,
        error_code: str,
        error_summary: str,
        next_retry_at: datetime | None,
        reached_max: bool,
    ) -> None:
        """发放尝试失败：达上限置 failed（清 next_retry），否则 retrying + 排下次重试。

        清空租约（owner/until），记末次错误。已 granted 不覆盖（幂等竞态兜底）。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel).filter_by(order_id=order_id).first()
            )
            if row is None or row.grant_status == "granted":
                return
            row.grant_status = "failed" if reached_max else "retrying"
            row.last_grant_error_code = error_code
            row.last_grant_error_summary = error_summary[:500]
            row.next_grant_retry_at = None if reached_max else next_retry_at
            row.grant_lease_until = None
            row.grant_lease_owner = None
            row.updated_at = utc_now()
            s.commit()
        finally:
            s.close()

    def mark_grant_retrying(
        self, *, order_id: str, error_code: str, error_summary: str
    ) -> None:
        """发放失败标记：grant_status=retrying + 记末次错误，保留 next_grant_retry_at 供重试。

        钱已入账、权益暂未发出——置为可重试态，等发放 worker/人工兜底（C-4/C-5）。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel).filter_by(order_id=order_id).first()
            )
            if row is not None and row.grant_status != "granted":
                row.grant_status = "retrying"
                row.last_grant_error_code = error_code
                row.last_grant_error_summary = error_summary[:500]
                row.updated_at = utc_now()
                s.commit()
        finally:
            s.close()

    def list_grant_orders(
        self,
        *,
        user_id: str | None = None,
        grant_status: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[PaymentOrder], int]:
        """支付发放管理列表（C-5，ADMIN）：限 paid 订单，按 paid_at DESC,order_id DESC。

        未指定 grant_status 时默认排除 granted（只看待办：pending/processing/retrying/
        failed/manual_review）。支持 user_id / grant_status / paid_at 区间过滤。返回
        （当页订单, 命中总数）。
        """
        s = self._s()
        try:
            q = s.query(orm.PaymentOrderModel).filter(
                orm.PaymentOrderModel.status == "paid"
            )
            if grant_status is not None:
                q = q.filter(orm.PaymentOrderModel.grant_status == grant_status)
            else:
                q = q.filter(orm.PaymentOrderModel.grant_status != "granted")
            if user_id is not None:
                q = q.filter(orm.PaymentOrderModel.user_id == user_id)
            if from_at is not None:
                q = q.filter(orm.PaymentOrderModel.paid_at >= from_at)
            if to_at is not None:
                q = q.filter(orm.PaymentOrderModel.paid_at < to_at)
            total = q.count()
            rows = (
                q.order_by(
                    orm.PaymentOrderModel.paid_at.desc(),
                    orm.PaymentOrderModel.order_id.desc(),
                )
                .offset(max(0, page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return [_to_order(r) for r in rows], total
        finally:
            s.close()

    def review_ambiguous_grant(
        self, *, order_id: str, reviewer: str, decision: str, reason: str
    ) -> PaymentOrder | None:
        """历史歧义订单人工复核（C-5，单事务）：锁 order → 按 decision 收敛 → 写审计。

        RETRY → 清歧义 + grant_status=pending + next_grant_retry_at=now（交回 worker）。
        MARK_GRANTED → 对账标记 granted（**不发权益**，历史已开通）+ granted_at。
        两者均记 grant_reviewed_at/by/decision（review_pair CHECK 要求成对）+ 清租约 +
        写一条 payment.review 审计（ADMIN，source_order_id=None 以免与发放审计唯一键冲突）。
        订单不存在返回 None；非历史歧义由调用方（service）前置拦截。
        """
        s = self._s()
        try:
            row = (
                s.query(orm.PaymentOrderModel)
                .filter_by(order_id=order_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return None
            now = utc_now()
            before = {
                "grant_status": row.grant_status,
                "historical_grant_ambiguous": row.historical_grant_ambiguous,
            }
            if decision == "RETRY":
                row.grant_status = "pending"
                row.next_grant_retry_at = now
            else:  # MARK_GRANTED：对账标记，不发权益
                row.grant_status = "granted"
                row.granted_at = now
                row.next_grant_retry_at = None
            row.historical_grant_ambiguous = False
            row.grant_reviewed_at = now
            row.grant_reviewed_by_user_id = reviewer
            row.grant_review_decision = decision
            row.grant_lease_owner = None
            row.grant_lease_until = None
            row.updated_at = now
            resource_type = (
                "MEMBERSHIP" if row.product_type == "membership" else "SUBSCRIPTION"
            )
            s.add(
                orm.EntitlementAuditModel(
                    audit_id=f"aud-{secrets.token_hex(12)}",
                    operation_id=None,
                    actor_user_id=reviewer,
                    target_user_id=row.user_id,
                    source_type="ADMIN",
                    action="payment.review",
                    resource_type=resource_type,
                    resource_id=row.product_ref,
                    reason=reason,
                    before_state=before,
                    after_state={
                        "grant_status": row.grant_status,
                        "historical_grant_ambiguous": False,
                        "decision": decision,
                    },
                    source_order_id=None,
                    created_at=now,
                )
            )
            s.commit()
            s.refresh(row)
            return _to_order(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def expire_stale(self, now: datetime | None = None) -> int:
        """把超过 expired_at 的 pending 订单置 expired（对账/展示用），返回条数。"""
        now = now or utc_now()
        s = self._s()
        try:
            rows = (
                s.query(orm.PaymentOrderModel)
                .filter(
                    orm.PaymentOrderModel.status == "pending",
                    orm.PaymentOrderModel.expired_at.isnot(None),
                    orm.PaymentOrderModel.expired_at < now,
                )
                .all()
            )
            for r in rows:
                r.status = "expired"
                r.updated_at = now
            s.commit()
            return len(rows)
        finally:
            s.close()
