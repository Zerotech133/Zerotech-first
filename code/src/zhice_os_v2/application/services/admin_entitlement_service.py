"""管理员权益原子事务服务（B1-6）。

每个写操作在单一数据库事务内完成：
  幂等 operation 查 →（同 key 同指纹 replay / 不同指纹 409）→ 锁 target user →
  锁 membership / 按 strategy_id 排序锁 subscriptions → 应用变更 + 版本递增 →
  写逐资源 audit → 写 completed operation → commit。任一步失败全回滚、不留 operation。

不复用各自 commit 的 MembershipRepository——那无法保证跨表原子性。直接操作 ORM。
"""
from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zhice_os_v2.domain.billing.admin_entitlement import (
    AdminWriteContext,
    AdminWriteResult,
    AuditQuery,
    EntitlementAudit,
)
from zhice_os_v2.domain.billing.entitlement import (
    is_subscription_active,
    membership_effective_status,
    membership_view,
    subscription_effective_status,
    subscription_view,
)
from zhice_os_v2.domain.strategy.eligibility import (
    deployment_enabled_markets,
    is_grantable_platform_strategy,
)
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


class EntitlementError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(12)}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class AdminEntitlementService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        enabled_markets: frozenset[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sf = session_factory or get_session
        self._enabled_markets = enabled_markets
        self._clock = clock

    def _markets(self) -> frozenset[str]:
        if self._enabled_markets is not None:
            return self._enabled_markets
        from zhice_os_v2.config.settings import get_settings

        return deployment_enabled_markets(get_settings().scheduler_markets)

    # ── 幂等事务框架 ────────────────────────────────────────────────
    def _replay_or_conflict(
        self, s: Session, ctx: AdminWriteContext
    ) -> AdminWriteResult:
        """回滚后重查同 (actor,key) 赢家：同指纹 → 幂等复放；异指纹 → 冲突。

        找不到赢家返回 None，交由调用方上抛原始错误（真实失败，非幂等竞态）。
        """
        winner = (
            s.query(orm.AdminOperationModel)
            .filter_by(
                actor_user_id=ctx.actor_user_id,
                idempotency_key=ctx.idempotency_key,
            )
            .first()
        )
        if winner is None:
            return None  # type: ignore[return-value]
        if winner.request_fingerprint == ctx.request_fingerprint:
            return AdminWriteResult(
                status_code=winner.response_status,
                body=winner.response_body,
                replayed=True,
            )
        raise EntitlementError("entitlements.idempotency_conflict") from None

    def _run(
        self,
        ctx: AdminWriteContext,
        action: str,
        target_user_id: str,
        mutate: Callable[[Session, datetime], tuple[dict, list[dict]]],
    ) -> AdminWriteResult:
        """在单事务内执行 mutate，写审计与幂等 operation。

        mutate(session, now) → (response_body, audits)。audits 每项含
        resource_type/resource_id/reason/before_state/after_state。

        并发处理：前置幂等查 → mutate → 提交。若 mutate 因并发竞态先失败（如另一
        事务已用同 key 完成写入、行锁串行化后本事务读到更高版本触发 version_conflict），
        回滚后重查 operation；同 key 赢家存在则按幂等语义收敛，否则上抛原始错误。
        """
        s = self._sf()
        try:
            existing = (
                s.query(orm.AdminOperationModel)
                .filter_by(
                    actor_user_id=ctx.actor_user_id,
                    idempotency_key=ctx.idempotency_key,
                )
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint == ctx.request_fingerprint:
                    return AdminWriteResult(
                        status_code=existing.response_status,
                        body=existing.response_body,
                        replayed=True,
                    )
                raise EntitlementError("entitlements.idempotency_conflict")

            now = self._clock()
            try:
                body, audits = mutate(s, now)
            except EntitlementError:
                # mutate 失败可能是并发竞态（赢家已用同 key 提交）：回滚重查。
                s.rollback()
                replay = self._replay_or_conflict(s, ctx)
                if replay is not None:
                    return replay
                raise  # 非幂等竞态 → 上抛真实错误（版本冲突/资格/缺失等）

            op_id = _new_id("op")
            for a in audits:
                s.add(
                    orm.EntitlementAuditModel(
                        audit_id=_new_id("aud"),
                        operation_id=op_id,
                        actor_user_id=ctx.actor_user_id,
                        target_user_id=target_user_id,
                        source_type="ADMIN",
                        action=action,
                        resource_type=a["resource_type"],
                        resource_id=a.get("resource_id"),
                        reason=a["reason"],
                        before_state=a["before_state"],
                        after_state=a["after_state"],
                        source_order_id=None,
                        created_at=now,
                    )
                )
            s.add(
                orm.AdminOperationModel(
                    operation_id=op_id,
                    actor_user_id=ctx.actor_user_id,
                    idempotency_key=ctx.idempotency_key,
                    request_fingerprint=ctx.request_fingerprint,
                    action=action,
                    target_user_id=target_user_id,
                    response_status=200,
                    response_body=body,
                    created_at=now,
                )
            )
            try:
                s.commit()
            except IntegrityError as ie:
                # 并发首次插入同 (actor,key)：回滚后读回赢家，按指纹 replay/conflict。
                # 只识别 operation 幂等键唯一约束；其他完整性错误（如权益唯一/外键）
                # 原样上抛，避免把真实错误误判为幂等冲突。
                s.rollback()
                if "uq_v2_admin_operation_actor_key" not in str(
                    getattr(ie, "orig", ie)
                ):
                    raise
                replay = self._replay_or_conflict(s, ctx)
                if replay is not None:
                    return replay
                raise EntitlementError("entitlements.idempotency_conflict") from None
            return AdminWriteResult(status_code=200, body=body, replayed=False)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ── 目标用户校验 ────────────────────────────────────────────────
    @staticmethod
    def _lock_user(s: Session, user_id: str) -> orm.UserModel:
        row = (
            s.query(orm.UserModel)
            .filter_by(user_id=user_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise EntitlementError("entitlements.user_not_found")
        return row

    # ── 会员 ────────────────────────────────────────────────────────
    def _membership_snapshot(self, row: orm.MembershipModel | None, now) -> dict:
        if row is None:
            return {"effective_status": "free", "version": 0, "is_permanent": False}
        from zhice_os_v2.infrastructure.repositories.membership_repository import (
            _to_membership,
        )

        m = _to_membership(row)
        return {
            "effective_status": membership_effective_status(m, now),
            "version": m.version,
            "is_permanent": m.is_permanent,
            "valid_until": _iso(m.valid_until),
        }

    def set_membership(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        mode: Literal["until", "permanent"],
        valid_until: datetime | None,
        reason: str,
        expected_version: int,
    ) -> AdminWriteResult:
        def mutate(s: Session, now: datetime):
            user = self._lock_user(s, target_user_id)
            if not user.is_active:
                raise EntitlementError("entitlements.user_inactive")
            if mode == "until":
                if valid_until is None or valid_until.tzinfo is None:
                    raise EntitlementError("entitlements.invalid_expiry")
                if valid_until.astimezone(UTC) <= now:
                    raise EntitlementError("entitlements.invalid_expiry")
            elif mode == "permanent":
                if valid_until is not None:
                    raise EntitlementError("entitlements.invalid_expiry")

            row = (
                s.query(orm.MembershipModel)
                .filter_by(user_id=target_user_id)
                .with_for_update()
                .one_or_none()
            )
            current_version = row.version if row is not None else 0
            if current_version != expected_version:
                raise EntitlementError("entitlements.version_conflict")

            before = self._membership_snapshot(row, now)
            if row is None:
                row = orm.MembershipModel(
                    user_id=target_user_id, tier="member", status="active",
                    version=1, created_at=now, updated_at=now,
                )
                s.add(row)
            else:
                row.tier = "member"
                row.status = "active"
                row.version += 1
                row.updated_at = now
            row.revoked_at = None
            row.revoked_by_user_id = None
            if mode == "permanent":
                row.is_permanent = True
                row.valid_until = None
            else:
                assert valid_until is not None  # mode=="until" 已校验非空
                row.is_permanent = False
                row.valid_until = valid_until.astimezone(UTC)
            s.flush()
            after = self._membership_snapshot(row, now)
            body = {"membership": after}
            audits = [{
                "resource_type": "MEMBERSHIP", "resource_id": None, "reason": reason,
                "before_state": before, "after_state": after,
            }]
            return body, audits

        return self._run(context, "membership.set", target_user_id, mutate)

    def _apply_membership_revocation(
        self,
        s: Session,
        *,
        target_user_id: str,
        expected_version: int,
        now: datetime,
        actor_user_id: str,
    ) -> tuple[dict, dict, bool]:
        """会员撤销的共享真相源：锁 user→锁 membership→校验→置 revoked/version++。

        返回 ``(before_snapshot, after_snapshot, noop)``；``noop=True`` 表示已撤销
        （after==before，不递增版本）。``revoke_membership_core`` 与 ``revoke_membership``
        的 mutate 均调用此方法，避免两处会员行变更逻辑手工同步漂移。
        """
        self._lock_user(s, target_user_id)  # inactive 允许撤销
        row = (
            s.query(orm.MembershipModel)
            .filter_by(user_id=target_user_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None or row.tier == "free":
            raise EntitlementError("entitlements.membership_not_granted")
        if row.version != expected_version:
            raise EntitlementError("entitlements.version_conflict")
        before = self._membership_snapshot(row, now)
        # 已撤销（revoked_at 非空）→ no-op，不重复撤销、不递增版本；否则执行撤销。
        noop = row.revoked_at is not None
        if not noop:
            row.status = "revoked"
            row.revoked_at = now
            row.revoked_by_user_id = actor_user_id
            row.version += 1
            row.updated_at = now
            s.flush()
        after = self._membership_snapshot(row, now)
        return before, after, noop

    def revoke_membership_core(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        reason: str,
        expected_version: int,
    ) -> AdminWriteResult:
        def mutate(s: Session, now: datetime):
            before, after, noop = self._apply_membership_revocation(
                s,
                target_user_id=target_user_id,
                expected_version=expected_version,
                now=now,
                actor_user_id=context.actor_user_id,
            )
            audits = [{
                "resource_type": "MEMBERSHIP", "resource_id": None, "reason": reason,
                "before_state": before, "after_state": after,
            }]
            body: dict = {"membership": after}
            if noop:
                body["noop"] = True
            return body, audits

        return self._run(context, "membership.revoke", target_user_id, mutate)

    def revoke_membership(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        reason: str,
        expected_version: int,
    ) -> AdminWriteResult:
        """会员撤销完整入口（B2-1）：同事务内先撤会员，再停"新增开仓风险"。

        与 ``revoke_membership_core`` 的差异：core 只改会员行；本入口在 **同一 session/
        事务** 内追加调用 ``EntitlementRiskRepository.stop_new_opening_risk`` —— 停用该用户
        自建私有策略的启用实例、取消其排队开仓（未成交计划 / 未提交 BUY 审批 / OPEN-BUY-NEW
        无提交 request 的意图）、释放对应 RESERVED 预留。任一步失败整体回滚、不留 operation。

        会员已撤销（noop）时仍执行清理（天然幂等：无启用实例即无可清理项）。
        """
        from zhice_os_v2.infrastructure.repositories.entitlement_risk_repository import (
            EntitlementRiskRepository,
            SimulatedOnlyBrokerCancellationOutbox,
        )

        def mutate(s: Session, now: datetime):
            before, after, noop = self._apply_membership_revocation(
                s,
                target_user_id=target_user_id,
                expected_version=expected_version,
                now=now,
                actor_user_id=context.actor_user_id,
            )

            # 同事务停止新增开仓风险（自建私有实例）。
            cleanup = EntitlementRiskRepository().stop_new_opening_risk(
                s,
                actor_user_id=context.actor_user_id,
                target_user_id=target_user_id,
                now=now,
                cancellation_outbox=SimulatedOnlyBrokerCancellationOutbox(),
            )
            cleanup_body = {
                "disabled_instance_ids": list(cleanup.disabled_instance_ids),
                "cancelled_plan_ids": list(cleanup.cancelled_plan_ids),
                "cancelled_approval_ids": list(cleanup.cancelled_approval_ids),
                "cancelled_intent_ids": list(cleanup.cancelled_intent_ids),
                "cancelled_request_ids": list(cleanup.cancelled_request_ids),
                "released_reservation_ids": list(cleanup.released_reservation_ids),
                "released_amount": str(cleanup.released_amount),
                "pending_external_cancellations": list(
                    cleanup.pending_external_cancellations
                ),
            }
            body: dict = {"membership": after, "opening_risk_cleanup": cleanup_body}
            if noop:
                body["noop"] = True
            audits = [{
                "resource_type": "MEMBERSHIP", "resource_id": None, "reason": reason,
                "before_state": before, "after_state": after,
            }]
            # 逐实例停用审计（便于追溯撤权连带影响）。
            for iid in cleanup.disabled_instance_ids:
                audits.append({
                    "resource_type": "USER_STRATEGY_INSTANCE", "resource_id": iid,
                    "reason": reason,
                    "before_state": {"enabled": True},
                    "after_state": {"enabled": False},
                })
            return body, audits

        return self._run(context, "membership.revoke", target_user_id, mutate)

    # ── 订阅 ────────────────────────────────────────────────────────
    def _sub_snapshot(self, row: orm.StrategySubscriptionModel | None, now) -> dict:
        if row is None:
            return {"effective_status": "expired", "version": 0, "is_permanent": False}
        from zhice_os_v2.infrastructure.repositories.membership_repository import (
            _to_subscription,
        )

        sub = _to_subscription(row)
        return {
            "strategy_id": row.strategy_id,
            "effective_status": subscription_effective_status(sub, now),
            "version": sub.version,
            "is_permanent": sub.is_permanent,
            "valid_until": _iso(sub.valid_until),
        }

    def _assert_grantable(self, s: Session, strategy_id: str) -> None:
        row = s.query(orm.StrategyModel).filter_by(strategy_id=strategy_id).one_or_none()
        if row is None:
            raise EntitlementError("entitlements.strategy_not_grantable")
        strat = Strategy(
            strategy_id=row.strategy_id,
            strategy_name=getattr(row, "strategy_name", row.strategy_id),
            market=row.market,
            status=StrategyStatus(row.status) if isinstance(row.status, str) else row.status,
            owner_user_id=getattr(row, "owner_user_id", None),
            visibility=getattr(row, "visibility", "PUBLIC"),
            publication_status=getattr(row, "publication_status", "PUBLISHED"),
        )
        if not is_grantable_platform_strategy(strat, enabled_markets=self._markets()):
            raise EntitlementError("entitlements.strategy_not_grantable")

    @staticmethod
    def _dedup_strategies(strategy_ids: Sequence[str]) -> list[str]:
        ordered = sorted(set(strategy_ids))
        if not (1 <= len(ordered) <= 100):
            raise EntitlementError("entitlements.invalid_batch")
        return ordered

    def set_subscriptions(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        strategy_ids: Sequence[str],
        mode: Literal["until", "permanent"],
        valid_until: datetime | None,
        reason: str,
        expected_versions: Mapping[str, int],
    ) -> AdminWriteResult:
        ids = self._dedup_strategies(strategy_ids)
        if set(expected_versions.keys()) != set(ids):
            raise EntitlementError("entitlements.versions_mismatch")
        if mode == "until":
            if valid_until is None or valid_until.tzinfo is None:
                raise EntitlementError("entitlements.invalid_expiry")

        def mutate(s: Session, now: datetime):
            user = self._lock_user(s, target_user_id)
            if not user.is_active:
                raise EntitlementError("entitlements.user_inactive")
            if mode == "until":
                assert valid_until is not None  # 已在 mutate 外校验非空
                if valid_until.astimezone(UTC) <= now:
                    raise EntitlementError("entitlements.invalid_expiry")
            results = []
            audits = []
            for sid in ids:  # 已排序 → 稳定锁序
                self._assert_grantable(s, sid)
                row = (
                    s.query(orm.StrategySubscriptionModel)
                    .filter_by(user_id=target_user_id, strategy_id=sid)
                    .with_for_update()
                    .one_or_none()
                )
                current_version = row.version if row is not None else 0
                if current_version != expected_versions[sid]:
                    raise EntitlementError("entitlements.version_conflict")
                before = self._sub_snapshot(row, now)
                if row is None:
                    row = orm.StrategySubscriptionModel(
                        user_id=target_user_id, strategy_id=sid, status="active",
                        version=1, created_at=now, updated_at=now,
                    )
                    s.add(row)
                else:
                    row.status = "active"
                    row.version += 1
                    row.updated_at = now
                row.revoked_at = None
                row.revoked_by_user_id = None
                if mode == "permanent":
                    row.is_permanent = True
                    row.valid_until = None
                else:
                    assert valid_until is not None  # mode=="until" 已校验非空
                    row.is_permanent = False
                    row.valid_until = valid_until.astimezone(UTC)
                s.flush()
                after = self._sub_snapshot(row, now)
                results.append(after)
                audits.append({
                    "resource_type": "SUBSCRIPTION", "resource_id": sid,
                    "reason": reason, "before_state": before, "after_state": after,
                })
            return {"subscriptions": results}, audits

        return self._run(context, "subscriptions.set", target_user_id, mutate)

    def revoke_subscriptions(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        strategy_ids: Sequence[str],
        reason: str,
        expected_versions: Mapping[str, int],
    ) -> AdminWriteResult:
        ids = self._dedup_strategies(strategy_ids)
        if set(expected_versions.keys()) != set(ids):
            raise EntitlementError("entitlements.versions_mismatch")

        def mutate(s: Session, now: datetime):
            self._lock_user(s, target_user_id)  # inactive 允许
            results = []
            audits = []
            for sid in ids:
                row = (
                    s.query(orm.StrategySubscriptionModel)
                    .filter_by(user_id=target_user_id, strategy_id=sid)
                    .with_for_update()
                    .one_or_none()
                )
                if row is None:
                    raise EntitlementError("entitlements.subscription_not_granted")
                if row.version != expected_versions[sid]:
                    raise EntitlementError("entitlements.version_conflict")
                before = self._sub_snapshot(row, now)
                if row.revoked_at is None:
                    row.status = "revoked"
                    row.revoked_at = now
                    row.revoked_by_user_id = context.actor_user_id
                    row.version += 1
                    row.updated_at = now
                    s.flush()
                after = self._sub_snapshot(row, now)
                results.append(after)
                audits.append({
                    "resource_type": "SUBSCRIPTION", "resource_id": sid,
                    "reason": reason, "before_state": before, "after_state": after,
                })
            return {"subscriptions": results}, audits

        return self._run(context, "subscriptions.revoke", target_user_id, mutate)

    # ── 角色变更（B1-9，复用 A 锁序） ──────────────────────────────
    def change_role(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        new_role: str,
        reason: str,
        expected_auth_version: int,
    ) -> AdminWriteResult:
        """审计事务内改角色：A 锁序（active ADMIN id 升序 → target）+ 乐观锁 +
        最后管理员保护 + auth_version+1 + ROLE audit。冲突/保护抛错整体回滚不留 operation。
        """
        def mutate(s: Session, now: datetime):
            active_admins = (
                s.query(orm.UserModel)
                .filter_by(role="ADMIN", is_active=True)
                .order_by(orm.UserModel.id.asc())
                .with_for_update()
                .all()
            )
            row = (
                s.query(orm.UserModel)
                .filter_by(user_id=target_user_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise EntitlementError("auth.user_not_found")
            if row.auth_version != expected_auth_version:
                raise EntitlementError("auth.auth_version_conflict")
            removing_admin = (
                row.role == "ADMIN" and row.is_active and new_role != "ADMIN"
            )
            if removing_admin and len(active_admins) <= 1:
                raise EntitlementError("auth.last_admin_protected")
            before = {"role": row.role, "auth_version": row.auth_version}
            row.role = new_role
            row.auth_version += 1
            s.flush()
            after = {"role": row.role, "auth_version": row.auth_version}
            body = {
                "user_id": row.user_id, "username": row.username,
                "role": row.role, "auth_version": row.auth_version,
            }
            audits = [{
                "resource_type": "ROLE", "resource_id": None, "reason": reason,
                "before_state": before, "after_state": after,
            }]
            return body, audits

        return self._run(context, "role.change", target_user_id, mutate)

    # ── 旧 grant 月度叠加（B1-9，接入审计事务 + 资格谓词） ─────────
    @staticmethod
    def _extend_base(valid_until, is_permanent: bool, revoked_at, now: datetime):
        """月度叠加基准：有效定期 → max(now, 旧到期)；否则 now。"""
        from zhice_os_v2.domain.billing.entitlement import _as_aware

        if valid_until is not None and not is_permanent and revoked_at is None:
            vu = _as_aware(valid_until)
            if vu > now:
                return vu
        return now

    def grant_membership_months(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        months: int,
        reason: str,
        expected_version: int,
    ) -> AdminWriteResult:
        """客服补单：会员续费 months 月（叠加）。永久会员收月费 no-op 不降级。"""
        if months < 1:
            raise EntitlementError("entitlements.invalid_batch")

        def mutate(s: Session, now: datetime):
            self._lock_user(s, target_user_id)
            row = (
                s.query(orm.MembershipModel)
                .filter_by(user_id=target_user_id)
                .with_for_update()
                .one_or_none()
            )
            current_version = row.version if row is not None else 0
            if current_version != expected_version:
                raise EntitlementError("entitlements.version_conflict")
            before = self._membership_snapshot(row, now)
            # 永久且未撤销 → no-op 不降级（不递增版本）。
            if row is not None and row.is_permanent and row.revoked_at is None:
                audits = [{
                    "resource_type": "MEMBERSHIP", "resource_id": None,
                    "reason": reason, "before_state": before, "after_state": before,
                }]
                return {"membership": before, "noop": True}, audits
            base = self._extend_base(
                None if row is None else row.valid_until,
                False if row is None else row.is_permanent,
                None if row is None else row.revoked_at,
                now,
            )
            new_valid_until = base + timedelta(days=30 * months)
            if row is None:
                row = orm.MembershipModel(
                    user_id=target_user_id, tier="member", status="active",
                    version=1, created_at=now, updated_at=now,
                )
                s.add(row)
            else:
                row.tier = "member"
                row.status = "active"
                row.version += 1
                row.updated_at = now
            row.revoked_at = None
            row.revoked_by_user_id = None
            row.is_permanent = False
            row.valid_until = new_valid_until
            s.flush()
            after = self._membership_snapshot(row, now)
            audits = [{
                "resource_type": "MEMBERSHIP", "resource_id": None, "reason": reason,
                "before_state": before, "after_state": after,
            }]
            return {"membership": after}, audits

        return self._run(context, "membership.grant", target_user_id, mutate)

    def grant_subscription_months(
        self,
        *,
        context: AdminWriteContext,
        target_user_id: str,
        strategy_id: str,
        months: int,
        reason: str,
        expected_version: int,
    ) -> AdminWriteResult:
        """客服补单：单策略订阅续订 months 月（叠加）。永久订阅 no-op 不降级。"""
        if months < 1:
            raise EntitlementError("entitlements.invalid_batch")

        def mutate(s: Session, now: datetime):
            self._lock_user(s, target_user_id)
            self._assert_grantable(s, strategy_id)
            row = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=target_user_id, strategy_id=strategy_id)
                .with_for_update()
                .one_or_none()
            )
            current_version = row.version if row is not None else 0
            if current_version != expected_version:
                raise EntitlementError("entitlements.version_conflict")
            before = self._sub_snapshot(row, now)
            if row is not None and row.is_permanent and row.revoked_at is None:
                audits = [{
                    "resource_type": "SUBSCRIPTION", "resource_id": strategy_id,
                    "reason": reason, "before_state": before, "after_state": before,
                }]
                return {"subscription": before, "noop": True}, audits
            base = self._extend_base(
                None if row is None else row.valid_until,
                False if row is None else row.is_permanent,
                None if row is None else row.revoked_at,
                now,
            )
            new_valid_until = base + timedelta(days=30 * months)
            if row is None:
                row = orm.StrategySubscriptionModel(
                    user_id=target_user_id, strategy_id=strategy_id, status="active",
                    version=1, created_at=now, updated_at=now,
                )
                s.add(row)
            else:
                row.status = "active"
                row.version += 1
                row.updated_at = now
            row.revoked_at = None
            row.revoked_by_user_id = None
            row.is_permanent = False
            row.valid_until = new_valid_until
            s.flush()
            after = self._sub_snapshot(row, now)
            audits = [{
                "resource_type": "SUBSCRIPTION", "resource_id": strategy_id,
                "reason": reason, "before_state": before, "after_state": after,
            }]
            return {"subscription": after}, audits

        return self._run(context, "subscription.grant", target_user_id, mutate)

    # ── 只读查询（B1-7） ────────────────────────────────────────────
    @staticmethod
    def _audit_view(a: EntitlementAudit) -> dict:
        return {
            "audit_id": a.audit_id,
            "operation_id": a.operation_id,
            "actor_user_id": a.actor_user_id,
            "target_user_id": a.target_user_id,
            "source_type": a.source_type,
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "reason": a.reason,
            "before_state": a.before_state,
            "after_state": a.after_state,
            "source_order_id": a.source_order_id,
            "created_at": _iso(a.created_at),
        }

    def list_users(
        self, *, page: int = 1, page_size: int = 20, query: str | None = None
    ) -> dict:
        """管理员用户摘要分页：会员实时状态 + 有效订阅计数（实时谓词，非 status）。"""
        from zhice_os_v2.infrastructure.repositories.membership_repository import (
            _to_membership,
            _to_subscription,
        )

        page = max(1, page)
        page_size = min(100, max(1, page_size))
        now = self._clock()
        s = self._sf()
        try:
            q = s.query(orm.UserModel)
            if query:
                like = f"%{query.strip()}%"
                q = q.filter(
                    orm.UserModel.username.like(like)
                    | orm.UserModel.user_id.like(like)
                )
            total = q.count()
            rows = (
                q.order_by(
                    orm.UserModel.created_at.desc(),
                    orm.UserModel.user_id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            user_ids = [r.user_id for r in rows]
            memberships = {
                m.user_id: m
                for m in s.query(orm.MembershipModel).filter(
                    orm.MembershipModel.user_id.in_(user_ids)
                ).all()
            } if user_ids else {}
            subs_by_user: dict[str, list] = {}
            if user_ids:
                for sub in s.query(orm.StrategySubscriptionModel).filter(
                    orm.StrategySubscriptionModel.user_id.in_(user_ids)
                ).all():
                    subs_by_user.setdefault(sub.user_id, []).append(sub)

            items = []
            for r in rows:
                m_row = memberships.get(r.user_id)
                m = _to_membership(m_row) if m_row is not None else None
                active_subs = sum(
                    1
                    for sub in subs_by_user.get(r.user_id, [])
                    if is_subscription_active(_to_subscription(sub), now)
                )
                items.append({
                    "user_id": r.user_id,
                    "username": r.username,
                    "role": r.role,
                    "is_active": r.is_active,
                    "auth_version": getattr(r, "auth_version", 1),
                    "membership": {
                        "effective_status": membership_effective_status(m, now),
                        "is_permanent": bool(m.is_permanent) if m else False,
                        "valid_until": _iso(m.valid_until) if m else None,
                        "version": m.version if m else 0,
                    },
                    "active_subscription_count": active_subs,
                })
            return {
                "items": items, "total": total, "page": page, "page_size": page_size,
            }
        finally:
            s.close()

    def get_user_detail(self, user_id: str) -> dict:
        """单用户权益详情：会员视图（缺失投影 free/version 0）+ 全部订阅（按 strategy_id
        排序，含 active/permanent/expired/revoked）+ 最近审计（≤20）+ 支付发放摘要。"""
        from zhice_os_v2.infrastructure.repositories.admin_entitlement_repository import (
            AdminEntitlementRepository,
        )
        from zhice_os_v2.infrastructure.repositories.membership_repository import (
            _to_membership,
            _to_subscription,
        )

        now = self._clock()
        s = self._sf()
        try:
            user = (
                s.query(orm.UserModel).filter_by(user_id=user_id).one_or_none()
            )
            if user is None:
                raise EntitlementError("entitlements.user_not_found")
            m_row = (
                s.query(orm.MembershipModel).filter_by(user_id=user_id).one_or_none()
            )
            m = _to_membership(m_row) if m_row is not None else None
            sub_rows = (
                s.query(orm.StrategySubscriptionModel)
                .filter_by(user_id=user_id)
                .order_by(orm.StrategySubscriptionModel.strategy_id.asc())
                .all()
            )
            subscriptions = [
                subscription_view(_to_subscription(row), now) for row in sub_rows
            ]
            # 支付发放摘要（C 前旧口径）：paid 且未发放 → pending，其余 0。
            pending = (
                s.query(orm.PaymentOrderModel)
                .filter(
                    orm.PaymentOrderModel.user_id == user_id,
                    orm.PaymentOrderModel.status == "paid",
                    orm.PaymentOrderModel.granted_at.is_(None),
                )
                .count()
            )
        finally:
            s.close()

        recent = AdminEntitlementRepository(session_factory=self._sf).recent_audits(
            user_id, limit=20
        )
        return {
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active,
                "auth_version": getattr(user, "auth_version", 1),
                "email": getattr(user, "email", None),
            },
            "membership": membership_view(m, user_id=user_id, now=now),
            "subscriptions": subscriptions,
            "recent_audits": [self._audit_view(a) for a in recent],
            "payment_grant_summary": {"pending": pending},
        }

    def list_audits(self, query: AuditQuery) -> dict:
        """审计分页（委托仓储；page_size 由调用方 API 限幅到 ≤200）。"""
        from zhice_os_v2.infrastructure.repositories.admin_entitlement_repository import (
            AdminEntitlementRepository,
        )

        rows, total = AdminEntitlementRepository(
            session_factory=self._sf
        ).list_audits(query)
        return {
            "items": [self._audit_view(a) for a in rows],
            "total": total,
            "page": max(1, query.page),
            "page_size": max(1, query.page_size),
        }
