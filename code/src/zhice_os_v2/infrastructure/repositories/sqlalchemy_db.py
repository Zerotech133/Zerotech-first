"""SQLAlchemy 持久化实现。

实现 PersistencePort 协议，业务层无需感知 SQL。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast as typing_cast

from sqlalchemy import Table, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from zhice_os_v2.domain.alert.alert import Alert, AlertLevel, AlertStatus
from zhice_os_v2.domain.approval.approval import ApprovalChannel, ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.candidate.candidate import Candidate, CandidatePool
from zhice_os_v2.domain.learning.learning import LearningCase
from zhice_os_v2.domain.planning.trade_plan import PlanStatus, TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.domain.replay.replay import ReplayCase, ReviewStatus
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import (
    Strategy,
    StrategyPublicationRequest,
    StrategyStatus,
    StrategyVersion,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import _engine, get_session


def _dt(value: datetime | None) -> datetime | None:
    return value


_ALERT_TABLE_READY = False


def _ensure_alert_table() -> None:
    """按需建 v2_alert 表（checkfirst 幂等）——迁移未覆盖该表，保留原懒建行为。"""
    global _ALERT_TABLE_READY
    if not _ALERT_TABLE_READY:
        typing_cast(Table, orm.AlertModel.__table__).create(bind=_engine(), checkfirst=True)
        _ALERT_TABLE_READY = True


def _to_alert(r: orm.AlertModel) -> Alert:
    # 容错：历史行可能存 V1 细分级别（WARN_HIGH/WARN_LOW），归一到合法枚举 WARN
    level = r.level if r.level in AlertLevel.__members__ else "WARN"
    return Alert(
        alert_id=r.alert_id,
        level=AlertLevel(level),
        title=r.title,
        message=r.message or "",
        status=AlertStatus(r.status),
        created_at=r.created_at,
        acked_at=r.acked_at,
    )


def _candidate_id_from_plan_id(plan_id: str) -> str:
    """从 plan_id 无损反解 candidate_id。

    create_from_candidate 构造 plan_id=f"plan-{candidate_id}"，承载候选血缘。TradePlan
    domain 无 candidate_id 字段，落库时从 plan_id 反解回填该列（此前恒写 ""，致 audit_trace
    按 candidate_id JOIN 血缘全断）。非 plan- 约定 id（seed/测试）返回 ""，不误伤。
    """
    return plan_id.removeprefix("plan-") if plan_id.startswith("plan-") else ""


class SqlAlchemyDatabase:
    def __init__(self, session: Session | None = None) -> None:
        self._session_factory = lambda: session if session is not None else get_session()

    # ---------- Strategy ----------
    def save_strategy(self, strategy: Strategy) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.StrategyModel).where(orm.StrategyModel.strategy_id == strategy.strategy_id)).scalar_one_or_none()
            if row is None:
                row = orm.StrategyModel(
                    strategy_id=strategy.strategy_id,
                    strategy_name=strategy.strategy_name,
                    market=strategy.market,
                    strategy_type=strategy.strategy_type,
                    status=str(strategy.status),
                    description=strategy.description,
                    owner_user_id=strategy.owner_user_id,
                    visibility=strategy.visibility,
                    publication_status=strategy.publication_status,
                    created_at=strategy.created_at,
                    updated_at=strategy.updated_at,
                )
                db.add(row)
            else:
                row.strategy_name = strategy.strategy_name
                row.market = strategy.market
                row.strategy_type = strategy.strategy_type
                row.status = str(strategy.status)
                row.description = strategy.description
                row.owner_user_id = strategy.owner_user_id
                row.visibility = strategy.visibility
                row.publication_status = strategy.publication_status
                row.updated_at = strategy.updated_at
            db.commit()

    def get_strategy(self, strategy_id: str) -> Strategy | None:
        with self._session_factory() as db:
            row = db.execute(select(orm.StrategyModel).where(orm.StrategyModel.strategy_id == strategy_id)).scalar_one_or_none()
            if row is None:
                return None
            return Strategy(
                strategy_id=row.strategy_id,
                strategy_name=row.strategy_name,
                market=row.market,
                strategy_type=row.strategy_type,
                status=StrategyStatus(row.status),
                description=row.description or "",
                owner_user_id=row.owner_user_id,
                visibility=row.visibility or "PUBLIC",
                publication_status=row.publication_status or "PUBLISHED",
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def list_strategies(self) -> list[Strategy]:
        with self._session_factory() as db:
            rows = db.execute(select(orm.StrategyModel)).scalars().all()
            return [
                Strategy(
                    strategy_id=r.strategy_id,
                    strategy_name=r.strategy_name,
                    market=r.market,
                    strategy_type=r.strategy_type,
                    status=StrategyStatus(r.status),
                    description=r.description or "",
                    owner_user_id=r.owner_user_id,
                    visibility=r.visibility or "PUBLIC",
                    publication_status=r.publication_status or "PUBLISHED",
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in rows
            ]

    def delete_strategy(self, strategy_id: str) -> None:
        """删策略 + 其所有版本（连带）。"""
        with self._session_factory() as db:
            db.execute(
                delete(orm.StrategyVersionModel).where(
                    orm.StrategyVersionModel.strategy_id == strategy_id
                )
            )
            db.execute(
                delete(orm.StrategyModel).where(orm.StrategyModel.strategy_id == strategy_id)
            )
            db.commit()

    def delete_strategy_version(self, version_id: str) -> None:
        with self._session_factory() as db:
            db.execute(
                delete(orm.StrategyVersionModel).where(
                    orm.StrategyVersionModel.strategy_version_id == version_id
                )
            )
            db.commit()

    # ---------- Strategy publication request ----------
    def save_publication_request(self, request: StrategyPublicationRequest) -> None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.StrategyPublicationRequestModel).where(
                    orm.StrategyPublicationRequestModel.request_id == request.request_id
                )
            ).scalar_one_or_none()
            if row is None:
                row = orm.StrategyPublicationRequestModel(
                    request_id=request.request_id,
                    source_strategy_id=request.source_strategy_id,
                    requester_user_id=request.requester_user_id,
                    status=request.status,
                    reviewed_by=request.reviewed_by,
                    reviewed_at=request.reviewed_at,
                    derived_strategy_id=request.derived_strategy_id,
                    created_at=request.created_at,
                )
                db.add(row)
            else:
                row.status = request.status
                row.reviewed_by = request.reviewed_by
                row.reviewed_at = request.reviewed_at
                row.derived_strategy_id = request.derived_strategy_id
            db.commit()

    def get_publication_request(
        self, request_id: str
    ) -> StrategyPublicationRequest | None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.StrategyPublicationRequestModel).where(
                    orm.StrategyPublicationRequestModel.request_id == request_id
                )
            ).scalar_one_or_none()
            return self._to_publication_request(row) if row is not None else None

    def get_pending_publication_request(
        self, source_strategy_id: str
    ) -> StrategyPublicationRequest | None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.StrategyPublicationRequestModel).where(
                    orm.StrategyPublicationRequestModel.source_strategy_id
                    == source_strategy_id,
                    orm.StrategyPublicationRequestModel.status == "PENDING",
                )
            ).scalar_one_or_none()
            return self._to_publication_request(row) if row is not None else None

    def _pre_commit_publication(self) -> None:
        """测试注入点：默认 no-op。原子性测试 monkeypatch 使其 raise，验证单事务回滚。"""

    def _upsert_strategy_row(self, db: Session, strategy: Strategy) -> None:
        row = db.execute(
            select(orm.StrategyModel).where(
                orm.StrategyModel.strategy_id == strategy.strategy_id
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                orm.StrategyModel(
                    strategy_id=strategy.strategy_id,
                    strategy_name=strategy.strategy_name,
                    market=strategy.market,
                    strategy_type=strategy.strategy_type,
                    status=str(strategy.status),
                    description=strategy.description,
                    owner_user_id=strategy.owner_user_id,
                    visibility=strategy.visibility,
                    publication_status=strategy.publication_status,
                    created_at=strategy.created_at,
                    updated_at=strategy.updated_at,
                )
            )
        else:
            row.strategy_name = strategy.strategy_name
            row.market = strategy.market
            row.strategy_type = strategy.strategy_type
            row.status = str(strategy.status)
            row.description = strategy.description
            row.owner_user_id = strategy.owner_user_id
            row.visibility = strategy.visibility
            row.publication_status = strategy.publication_status
            row.updated_at = strategy.updated_at

    def _upsert_version_row(self, db: Session, version: StrategyVersion) -> None:
        row = db.execute(
            select(orm.StrategyVersionModel).where(
                orm.StrategyVersionModel.strategy_version_id
                == version.strategy_version_id
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                orm.StrategyVersionModel(
                    strategy_version_id=version.strategy_version_id,
                    strategy_id=version.strategy_id,
                    version_no=version.version_no,
                    market=version.market,
                    status=str(version.status),
                    spec=version.spec.model_dump(),
                    research_ref=version.research_ref,
                    created_at=version.created_at,
                )
            )
        else:
            row.version_no = version.version_no
            row.market = version.market
            row.status = str(version.status)
            row.spec = version.spec.model_dump()
            row.research_ref = version.research_ref

    def commit_publication_approval(
        self,
        *,
        derived_strategy: Strategy,
        derived_version: StrategyVersion,
        source_strategy_id: str,
        request: StrategyPublicationRequest,
    ) -> None:
        """单会话单 commit：建派生策略+版本、reset 源、flip request=APPROVED。

        计划硬性要求"同一事务派生+置 APPROVED+写 derived_strategy_id"，消除"派生 PUBLIC
        已落库但 request 仍 PENDING"的未审批上线窗口。任一步失败经上下文管理器整体回滚。
        """
        from zhice_os_v2.domain.ontology.objects import utc_now

        reviewed_at = request.reviewed_at or utc_now()
        with self._session_factory() as db:
            self._upsert_strategy_row(db, derived_strategy)
            self._upsert_version_row(db, derived_version)
            src = db.execute(
                select(orm.StrategyModel).where(
                    orm.StrategyModel.strategy_id == source_strategy_id
                )
            ).scalar_one_or_none()
            if src is not None:
                src.publication_status = "PRIVATE"
                src.updated_at = reviewed_at
            rrow = db.execute(
                select(orm.StrategyPublicationRequestModel).where(
                    orm.StrategyPublicationRequestModel.request_id == request.request_id
                )
            ).scalar_one_or_none()
            if rrow is not None:
                rrow.status = request.status
                rrow.reviewed_by = request.reviewed_by
                rrow.reviewed_at = request.reviewed_at
                rrow.derived_strategy_id = request.derived_strategy_id
            self._pre_commit_publication()
            db.commit()

    def commit_publication_rejection(
        self, *, source_strategy_id: str, request: StrategyPublicationRequest
    ) -> None:
        """单会话单 commit：flip request=REJECTED、reset 源 publication_status=PRIVATE。

        不产生任何公共副本。任一步失败经上下文管理器整体回滚。
        """
        from zhice_os_v2.domain.ontology.objects import utc_now

        reviewed_at = request.reviewed_at or utc_now()
        with self._session_factory() as db:
            src = db.execute(
                select(orm.StrategyModel).where(
                    orm.StrategyModel.strategy_id == source_strategy_id
                )
            ).scalar_one_or_none()
            if src is not None:
                src.publication_status = "PRIVATE"
                src.updated_at = reviewed_at
            rrow = db.execute(
                select(orm.StrategyPublicationRequestModel).where(
                    orm.StrategyPublicationRequestModel.request_id == request.request_id
                )
            ).scalar_one_or_none()
            if rrow is not None:
                rrow.status = request.status
                rrow.reviewed_by = request.reviewed_by
                rrow.reviewed_at = request.reviewed_at
            self._pre_commit_publication()
            db.commit()

    @staticmethod
    def _to_publication_request(
        row: orm.StrategyPublicationRequestModel,
    ) -> StrategyPublicationRequest:
        return StrategyPublicationRequest(
            request_id=row.request_id,
            source_strategy_id=row.source_strategy_id,
            requester_user_id=row.requester_user_id,
            status=row.status,
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            derived_strategy_id=row.derived_strategy_id,
            created_at=row.created_at,
        )

    def save_strategy_version(self, version: StrategyVersion) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.StrategyVersionModel).where(orm.StrategyVersionModel.strategy_version_id == version.strategy_version_id)).scalar_one_or_none()
            if row is None:
                row = orm.StrategyVersionModel(
                    strategy_version_id=version.strategy_version_id,
                    strategy_id=version.strategy_id,
                    version_no=version.version_no,
                    market=version.market,
                    status=str(version.status),
                    spec=version.spec.model_dump(),
                    research_ref=version.research_ref,
                    created_at=version.created_at,
                )
                db.add(row)
            else:
                row.version_no = version.version_no
                row.market = version.market
                row.status = str(version.status)
                row.spec = version.spec.model_dump()
                row.research_ref = version.research_ref
            db.commit()

    def get_strategy_version(self, version_id: str) -> StrategyVersion | None:
        with self._session_factory() as db:
            row = db.execute(select(orm.StrategyVersionModel).where(orm.StrategyVersionModel.strategy_version_id == version_id)).scalar_one_or_none()
            if row is None:
                return None
            # 内置策略 spec={} / 坏 spec 时用默认兜底（与 list_strategy_versions 一致，避免崩）
            _default = {"spec_version": 1, "market": row.market, "universe": {"type": "all"}}
            try:
                spec = StrategySpec.model_validate(row.spec if row.spec else _default)
            except Exception:  # noqa: BLE001
                spec = StrategySpec.model_validate(_default)
            return StrategyVersion(
                strategy_version_id=row.strategy_version_id,
                strategy_id=row.strategy_id,
                version_no=row.version_no,
                market=row.market,
                spec=spec,
                status=StrategyStatus(row.status),
                research_ref=row.research_ref,
                created_at=row.created_at,
            )

    def list_strategy_versions(self) -> list[StrategyVersion]:
        _DEFAULT_SPEC = {"spec_version": 1, "market": "CN", "universe": {"type": "all"}}
        with self._session_factory() as db:
            rows = db.execute(select(orm.StrategyVersionModel)).scalars().all()
            result = []
            for r in rows:
                try:
                    spec = StrategySpec.model_validate(r.spec if r.spec else _DEFAULT_SPEC)
                except Exception:
                    spec = StrategySpec.model_validate({**_DEFAULT_SPEC, "market": r.market})
                result.append(StrategyVersion(
                    strategy_version_id=r.strategy_version_id,
                    strategy_id=r.strategy_id,
                    version_no=r.version_no,
                    market=r.market,
                    spec=spec,
                    status=StrategyStatus(r.status),
                    research_ref=r.research_ref,
                    created_at=r.created_at,
                ))
            return result

    def is_version_referenced(self, strategy_version_id: str) -> bool:
        """该版本是否被 UserStrategyInstance/TradePlan/Position/Replay 任一引用。

        不可变守卫：只要有一张表引用了该 strategy_version_id，就禁止改 spec（改则会
        无声改变历史/在运行实例的行为）。逐表 EXISTS 短路，命中即返回。
        """
        with self._session_factory() as db:
            for model in (
                orm.UserStrategyInstanceModel,
                orm.TradePlanModel,
                orm.PositionModel,
                orm.ReplayModel,
            ):
                hit = db.execute(
                    select(model.strategy_version_id)
                    .where(model.strategy_version_id == strategy_version_id)
                    .limit(1)
                ).first()
                if hit is not None:
                    return True
            return False

    # ---------- Candidate ----------
    def _upsert_candidate(self, db: Session, candidate: Candidate, pool_id: str) -> None:
        row = db.execute(
            select(orm.CandidateAssetModel).where(
                orm.CandidateAssetModel.candidate_id == candidate.candidate_id
            )
        ).scalar_one_or_none()
        values = dict(
            pool_id=pool_id,
            market=candidate.market,
            ts_code=candidate.ts_code,
            candidate_score=Decimal(str(candidate.score)),
            candidate_reason=candidate.reason,
            status=candidate.status,
        )
        if row is None:
            db.add(orm.CandidateAssetModel(
                candidate_id=candidate.candidate_id,
                created_at=candidate.created_at,
                **values,
            ))
        else:
            for k, v in values.items():
                setattr(row, k, v)

    def save_candidate_pool(self, pool: CandidatePool) -> None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.CandidatePoolModel).where(
                    orm.CandidatePoolModel.pool_id == pool.pool_id
                )
            ).scalar_one_or_none()
            values = dict(
                strategy_version_id=pool.strategy_version_id or "",
                market=pool.market,
                pool_date=pool.trade_date,
                pool_type="STRATEGY",
                batch_no=1,
                candidate_count=len(pool.candidates),
                status="ACTIVE",
            )
            if row is None:
                db.add(orm.CandidatePoolModel(
                    pool_id=pool.pool_id,
                    created_at=pool.created_at,
                    **values,
                ))
            else:
                for k, v in values.items():
                    setattr(row, k, v)
            for candidate in pool.candidates:
                self._upsert_candidate(db, candidate, pool.pool_id)
            db.commit()

    def save_candidate(self, candidate: Candidate) -> None:
        with self._session_factory() as db:
            self._upsert_candidate(db, candidate, "")
            db.commit()

    def list_candidates_by_strategy(self, strategy_version_id: str) -> list[Candidate]:
        with self._session_factory() as db:
            rows = db.execute(
                select(orm.CandidateAssetModel, orm.CandidatePoolModel.strategy_version_id)
                .join(
                    orm.CandidatePoolModel,
                    orm.CandidateAssetModel.pool_id == orm.CandidatePoolModel.pool_id,
                )
                .where(orm.CandidatePoolModel.strategy_version_id == strategy_version_id)
            ).all()
            result: list[Candidate] = []
            for asset, sv_id in rows:
                result.append(Candidate(
                    candidate_id=asset.candidate_id,
                    strategy_version_id=sv_id or "",
                    market=asset.market,
                    ts_code=asset.ts_code,
                    score=float(asset.candidate_score) if asset.candidate_score is not None else 0.0,
                    reason=asset.candidate_reason or "",
                    factor_values={},
                    created_at=asset.created_at,
                    status=asset.status,
                ))
            return result

    # ---------- TradePlan ----------
    def save_trade_plan(self, plan: TradePlan) -> None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.TradePlanModel).where(orm.TradePlanModel.plan_id == plan.plan_id)
            ).scalar_one_or_none()
            values = dict(
                market=plan.market,
                ts_code=plan.ts_code,
                plan_status=str(plan.status.value) if hasattr(plan.status, "value") else str(plan.status),
                ref_price=plan.ref_price,
                stop_loss_price=plan.stop_loss,
                take_profit_price=plan.take_profit,
                max_position_ratio=plan.max_position_ratio,
                # 运行时归属（Phase B）。legacy 写入路径传 None，行为不变。
                user_strategy_id=plan.user_strategy_id,
                account_id=plan.account_id,
            )
            if row is None:
                db.add(orm.TradePlanModel(
                    plan_id=plan.plan_id,
                    strategy_version_id=plan.strategy_version_id or "",
                    candidate_id=_candidate_id_from_plan_id(plan.plan_id),
                    created_at=plan.created_at,
                    **values,
                ))
            else:
                if row.plan_status in (
                    PlanStatus.CANCELLED,
                    PlanStatus.REJECTED,
                    PlanStatus.EXECUTED,
                    PlanStatus.EXPIRED,
                ):
                    return  # Don't overwrite terminal-state plans
                for k, v in values.items():
                    setattr(row, k, v)
            db.commit()

    def get_trade_plan(self, plan_id: str) -> TradePlan | None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.TradePlanModel).where(orm.TradePlanModel.plan_id == plan_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            return TradePlan(
                plan_id=row.plan_id,
                strategy_version_id=row.strategy_version_id or "",
                market=row.market,
                ts_code=row.ts_code,
                direction="BUY",
                qty=Decimal("0"),
                ref_price=row.ref_price or Decimal("0"),
                stop_loss=row.stop_loss_price,
                take_profit=row.take_profit_price,
                user_strategy_id=row.user_strategy_id,
                account_id=row.account_id,
            )

    def list_trade_plans(self) -> list[TradePlan]:
        with self._session_factory() as db:
            rows = db.execute(select(orm.TradePlanModel)).scalars().all()
            return [
                TradePlan(
                    plan_id=row.plan_id,
                    strategy_version_id=row.strategy_version_id or "",
                    market=row.market,
                    ts_code=row.ts_code,
                    direction="BUY",
                    qty=Decimal("0"),
                    ref_price=row.ref_price or Decimal("0"),
                    stop_loss=row.stop_loss_price,
                    take_profit=row.take_profit_price,
                    user_strategy_id=row.user_strategy_id,
                    account_id=row.account_id,
                )
                for row in rows
            ]

    def update_trade_plan_status(self, plan_id: str, status: str) -> None:
        with self._session_factory() as db:
            row = db.execute(
                select(orm.TradePlanModel).where(orm.TradePlanModel.plan_id == plan_id)
            ).scalar_one_or_none()
            if row is not None:
                row.plan_status = status
                db.commit()

    def expire_stale_pending_plans(self, market: str, session_date) -> dict:
        """口径A 同会话过期：market 下 创建日<session_date 且无 APPROVED 授权 的 PENDING
        计划置 EXPIRED，其 PENDING approval 一并置 EXPIRED（镜像 ApprovalRequest.expire()：
        仅 PENDING→EXPIRED + decided_at）。硬排除 approval=APPROVED；幂等（只动 PENDING）。
        """
        from sqlalchemy import func

        from zhice_os_v2.domain.ontology.objects import utc_now
        with self._session_factory() as db:
            approved_intents = set(db.execute(
                select(orm.ApprovalRequestModel.order_intent_id)
                .where(orm.ApprovalRequestModel.status == "APPROVED")
            ).scalars().all())
            rows = db.execute(
                select(orm.TradePlanModel).where(
                    orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status == "PENDING",
                    func.date(orm.TradePlanModel.created_at) < session_date,
                )
            ).scalars().all()
            expired_ids: list[str] = []
            for r in rows:
                if r.plan_id in approved_intents:
                    continue  # 硬排除已批准（保护在执行/已批准计划）
                r.plan_status = "EXPIRED"
                expired_ids.append(r.plan_id)
            m = 0
            if expired_ids:
                appr = db.execute(
                    select(orm.ApprovalRequestModel).where(
                        orm.ApprovalRequestModel.order_intent_id.in_(expired_ids),
                        orm.ApprovalRequestModel.status == "PENDING",
                    )
                ).scalars().all()
                for a in appr:
                    a.status = "EXPIRED"
                    a.decided_at = utc_now()
                    m += 1
            db.commit()
            return {"plans_expired": len(expired_ids), "approvals_expired": m}

    def expire_stale_active_approvals(self, market: str, session_date) -> dict:
        """日内单语义过期：market 下 created_at 日期<session_date 且无对应 OPEN 持仓 的
        非终态(PENDING/APPROVED/EXECUTING)承认置 EXPIRED，镜像其计划置 EXPIRED。

        根因（2026-09-02）：sim 自动批准的 APPROVED 若当日未约定则无终态收敛，逐日累积
        撑爆 sim_max_positions（pre_market 建计划前 active 承认计数早退）。此扫描使 active
        承认恒收敛到今日在途，并兜住计划已过期而承认孤儿滞留 PENDING 的历史残渣。硬保护
        OPEN 持仓对应承认；终态/今日在途不动。幂等。
        """
        from sqlalchemy import func

        from zhice_os_v2.domain.ontology.objects import utc_now
        with self._session_factory() as db:
            held_plan_ids = set(db.execute(
                select(orm.PositionModel.trade_plan_id)
                .where(orm.PositionModel.market == market,
                       orm.PositionModel.status == "OPEN",
                       orm.PositionModel.trade_plan_id.is_not(None))
            ).scalars().all())
            appr = db.execute(
                select(orm.ApprovalRequestModel).where(
                    orm.ApprovalRequestModel.market == market,
                    orm.ApprovalRequestModel.status.in_(("PENDING", "APPROVED", "EXECUTING")),
                    func.date(orm.ApprovalRequestModel.created_at) < session_date,
                )
            ).scalars().all()
            expired_intents: list[str] = []
            n = 0
            for a in appr:
                if a.order_intent_id in held_plan_ids:
                    continue  # 硬保护：已落 OPEN 持仓的承认不动
                a.status = "EXPIRED"
                a.decided_at = utc_now()
                expired_intents.append(a.order_intent_id)
                n += 1
            pm = 0
            if expired_intents:
                plans = db.execute(
                    select(orm.TradePlanModel).where(
                        orm.TradePlanModel.plan_id.in_(expired_intents),
                        orm.TradePlanModel.plan_status.in_(("PENDING", "APPROVED")),
                    )
                ).scalars().all()
                for p in plans:
                    p.plan_status = "EXPIRED"
                    pm += 1
            db.commit()
            return {"active_approvals_expired": n, "active_plans_expired": pm}

    # ---------- Approval ----------
    def save_approval(self, request: ApprovalRequest) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.ApprovalRequestModel).where(orm.ApprovalRequestModel.request_id == request.request_id)).scalar_one_or_none()
            if row is None:
                row = orm.ApprovalRequestModel(
                    request_id=request.request_id,
                    order_intent_id=request.order_intent_id,
                    strategy_version_id=request.strategy_version_id,
                    market=request.market,
                    ts_code=request.ts_code,
                    direction=request.direction,
                    qty=request.qty,
                    price=request.price,
                    reason=request.reason,
                    status=request.status.value,
                    channel=request.channel.value,
                    token=request.token,
                    auto_follow=request.auto_follow,
                    created_at=request.created_at,
                    decided_at=request.decided_at,
                    decided_by=request.decided_by,
                    reject_reason=request.reject_reason,
                    # 运行时归属（Phase B）。legacy 写入路径传 None，行为不变。
                    user_strategy_id=request.user_strategy_id,
                    account_id=request.account_id,
                )
                db.add(row)
            else:
                if row.status in (
                    ApprovalStatus.REJECTED,
                    ApprovalStatus.CANCELLED,
                    ApprovalStatus.EXECUTED,
                    ApprovalStatus.EXECUTING,
                    ApprovalStatus.PARTIALLY_EXECUTED,
                ):
                    return  # Don't overwrite terminal-state approvals
                row.status = request.status.value
                row.decided_at = request.decided_at
                row.decided_by = request.decided_by
                row.reject_reason = request.reject_reason
            db.commit()

    def get_approval(self, request_id: str) -> ApprovalRequest | None:
        with self._session_factory() as db:
            row = db.execute(select(orm.ApprovalRequestModel).where(orm.ApprovalRequestModel.request_id == request_id)).scalar_one_or_none()
            if row is None:
                return None
            return ApprovalRequest(
                request_id=row.request_id,
                order_intent_id=row.order_intent_id,
                strategy_version_id=row.strategy_version_id,
                market=row.market,
                ts_code=row.ts_code,
                direction=row.direction,
                qty=float(row.qty),
                price=float(row.price) if row.price is not None else None,
                reason=row.reason or "",
                status=ApprovalStatus(row.status),
                channel=ApprovalChannel(row.channel),
                token=row.token,
                auto_follow=row.auto_follow,
                created_at=row.created_at,
                decided_at=row.decided_at,
                decided_by=row.decided_by,
                reject_reason=row.reject_reason,
                user_strategy_id=row.user_strategy_id,
                account_id=row.account_id,
            )

    def list_approvals(self) -> list[ApprovalRequest]:
        with self._session_factory() as db:
            rows = db.execute(select(orm.ApprovalRequestModel)).scalars().all()
            return [
                ApprovalRequest(
                    request_id=r.request_id,
                    order_intent_id=r.order_intent_id,
                    strategy_version_id=r.strategy_version_id,
                    market=r.market,
                    ts_code=r.ts_code,
                    direction=r.direction,
                    qty=float(r.qty),
                    price=float(r.price) if r.price is not None else None,
                    reason=r.reason or "",
                    status=ApprovalStatus(r.status),
                    channel=ApprovalChannel(r.channel),
                    token=r.token,
                    auto_follow=r.auto_follow,
                    created_at=r.created_at,
                    decided_at=r.decided_at,
                    decided_by=r.decided_by,
                    reject_reason=r.reject_reason,
                    user_strategy_id=r.user_strategy_id,
                    account_id=r.account_id,
                )
                for r in rows
            ]

    def claim_approval_for_execution(self, request_id: str) -> bool:
        """原子性地将 APPROVED → EXECUTING，防止并发/重复执行。

        使用条件 UPDATE（WHERE status = 'APPROVED'），仅当行处于 APPROVED 状态时才更新。
        返回 True 表示成功获得执行权；False 表示已被其他路径领走（EXECUTING/EXECUTED），
        调用方应跳过本次执行。
        """
        with self._session_factory() as db:
            # typing_cast: Session.execute() stubs return Result[Any], but DML
            # actually returns CursorResult[Any] which has .rowcount.
            result = typing_cast(
                CursorResult,
                db.execute(
                    update(orm.ApprovalRequestModel)
                    .where(
                        orm.ApprovalRequestModel.request_id == request_id,
                        orm.ApprovalRequestModel.status == "APPROVED",
                    )
                    .values(status="EXECUTING")
                ),
            )
            db.commit()
            return result.rowcount > 0

    def release_approval_claim(self, request_id: str) -> bool:
        """claim 的逆操作：EXECUTING → APPROVED，回滚未成交的执行占位。

        fill 失败/异常时释放 claim，使承认恢复可执行（当日可重试），且不以固着 EXECUTING
        永久占用 sim_max_positions 上限。条件 UPDATE（WHERE status='EXECUTING'）保证幂等。
        """
        with self._session_factory() as db:
            result = typing_cast(
                CursorResult,
                db.execute(
                    update(orm.ApprovalRequestModel)
                    .where(
                        orm.ApprovalRequestModel.request_id == request_id,
                        orm.ApprovalRequestModel.status == "EXECUTING",
                    )
                    .values(status="APPROVED")
                ),
            )
            db.commit()
            return result.rowcount > 0

    def mark_approval_executed(self, request_id: str, status: str = "EXECUTED") -> bool:
        """成交完成终态：EXECUTING → EXECUTED（或 PARTIALLY_EXECUTED，超持仓卖出）。
        仅在 EXECUTING 态生效（幂等、防越权）。

        save_approval 把 EXECUTING 视为不可覆写终态，故 _auto_execute / 人工执行成交后经
        save_approval 无法落终态（全系统 0 个 EXECUTED、已成交承认永卡 EXECUTING 计入
        sim_max_positions 上限）。此条件 UPDATE 绕过 save_approval 守卫，为 claim 的正向收尾。
        """
        from zhice_os_v2.domain.ontology.objects import utc_now
        with self._session_factory() as db:
            result = typing_cast(
                CursorResult,
                db.execute(
                    update(orm.ApprovalRequestModel)
                    .where(
                        orm.ApprovalRequestModel.request_id == request_id,
                        orm.ApprovalRequestModel.status == "EXECUTING",
                    )
                    .values(status=status, decided_at=utc_now())
                ),
            )
            db.commit()
            return result.rowcount > 0

    # ---------- Position ----------
    def save_position(self, position: PositionLot) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.PositionModel).where(orm.PositionModel.position_id == position.position_id)).scalar_one_or_none()
            if row is None:
                row = orm.PositionModel(
                    position_id=position.position_id,
                    strategy_version_id=position.strategy_version_id,
                    market=position.market,
                    ts_code=position.ts_code,
                    direction=position.direction,
                    open_qty=position.open_qty,
                    open_price=position.open_price,
                    # 归属链：生成该持仓的计划 id（此前不写 → 恒 NULL，丢 plan→position 追溯）。
                    # 注：avg_cost 不单列落库——per-lot 模型下每笔成交为独立 lot，均价由跨 lot
                    # 聚合读时计算，无独立写入点（保持只补 trade_plan_id 的最小改动）。
                    trade_plan_id=position.trade_plan_id,
                    status=position.status.value,
                    close_qty=position.close_qty,
                    close_price=position.close_price,
                    pnl_amount=position.pnl_amount,
                    fee=position.fee,
                    opened_at=position.opened_at,
                    closed_at=position.closed_at,
                    # 运行时归属（Phase B）。legacy 写入路径传 None，行为不变。
                    user_strategy_id=position.user_strategy_id,
                    account_id=position.account_id,
                )
                db.add(row)
            else:
                row.open_qty = position.open_qty
                row.status = position.status.value
                row.close_qty = position.close_qty
                row.close_price = position.close_price
                # 仅在提供了 pnl_amount 时写入，避免覆盖旁路（workflow）已写好的值
                if position.pnl_amount is not None:
                    row.pnl_amount = position.pnl_amount
                row.fee = position.fee
                row.closed_at = position.closed_at
            db.commit()

    def get_position(self, position_id: str) -> PositionLot | None:
        with self._session_factory() as db:
            row = db.execute(select(orm.PositionModel).where(orm.PositionModel.position_id == position_id)).scalar_one_or_none()
            if row is None:
                return None
            return self._to_lot(row)

    def list_positions(self, market: str | None = None) -> list[PositionLot]:
        with self._session_factory() as db:
            stmt = select(orm.PositionModel)
            if market:
                stmt = stmt.where(orm.PositionModel.market == market)
            rows = db.execute(stmt).scalars().all()
            return [self._to_lot(r) for r in rows]

    def list_open_positions(self, market: str | None = None) -> list[PositionLot]:
        with self._session_factory() as db:
            stmt = select(orm.PositionModel).where(orm.PositionModel.status == "OPEN")
            if market:
                stmt = stmt.where(orm.PositionModel.market == market)
            rows = db.execute(stmt).scalars().all()
            return [self._to_lot(r) for r in rows]

    @staticmethod
    def _to_lot(r: orm.PositionModel) -> PositionLot:
        return PositionLot(
            position_id=r.position_id,
            strategy_version_id=r.strategy_version_id,
            market=r.market,
            ts_code=r.ts_code,
            direction=r.direction,
            open_qty=r.open_qty,
            open_price=r.open_price,
            trade_plan_id=r.trade_plan_id,
            status=PositionStatus(r.status) if r.status else PositionStatus.OPEN,
            close_qty=r.close_qty,
            close_price=r.close_price,
            pnl_amount=r.pnl_amount,
            fee=r.fee if r.fee is not None else Decimal("0"),
            opened_at=r.opened_at,
            closed_at=r.closed_at,
            user_strategy_id=r.user_strategy_id,
            account_id=r.account_id,
        )

    # ---------- Replay ----------
    def save_replay(self, replay: ReplayCase) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.ReplayModel).where(orm.ReplayModel.replay_id == replay.replay_id)).scalar_one_or_none()
            if row is None:
                row = orm.ReplayModel(
                    replay_id=replay.replay_id,
                    strategy_version_id=replay.strategy_version_id,
                    market=replay.market,
                    ts_code=replay.ts_code,
                    pnl=replay.pnl,
                    pnl_pct=replay.pnl_pct,
                    holding_period_seconds=replay.holding_period_seconds,
                    exit_reason=replay.exit_reason,
                    attribution=replay.attribution,
                    status=replay.status.value,
                    created_at=replay.created_at,
                    open_date=replay.open_date,
                    close_date=replay.close_date,
                    qty=replay.qty,
                    open_price=replay.open_price,
                    close_price=replay.close_price,
                    outcome=replay.outcome,
                    # 运行时归属（Phase B）。legacy 写入路径传 None，行为不变。
                    user_strategy_id=replay.user_strategy_id,
                    account_id=replay.account_id,
                )
                db.add(row)
            else:
                row.status = replay.status.value
                row.close_date = replay.close_date or row.close_date
                row.outcome = replay.outcome or row.outcome
            db.commit()

    # ---------- Replay list ----------
    def list_replays(self) -> list[ReplayCase]:
        with self._session_factory() as db:
            rows = db.execute(
                select(orm.ReplayModel)
            ).scalars().all()
            return [
                ReplayCase(
                    replay_id=r.replay_id,
                    strategy_version_id=r.strategy_version_id,
                    market=r.market,
                    ts_code=r.ts_code,
                    pnl=r.pnl,
                    pnl_pct=r.pnl_pct,
                    holding_period_seconds=r.holding_period_seconds,
                    exit_reason=r.exit_reason,
                    attribution=r.attribution,
                    status=ReviewStatus(r.status),
                    created_at=r.created_at,
                    user_strategy_id=r.user_strategy_id,
                    account_id=r.account_id,
                )
                for r in rows
            ]

    # ---------- Learning ----------
    def save_learning(self, learning: LearningCase) -> None:
        with self._session_factory() as db:
            row = db.execute(select(orm.LearningCaseModel).where(orm.LearningCaseModel.learning_id == learning.learning_id)).scalar_one_or_none()
            if row is None:
                row = orm.LearningCaseModel(
                    learning_id=learning.learning_id,
                    replay_id=learning.replay_id,
                    strategy_version_id=learning.strategy_version_id,
                    market=learning.market,
                    ts_code=learning.ts_code or "UNKNOWN",
                    outcome=learning.insight,
                    pnl_pct=learning.pnl_pct,
                    lesson_summary=learning.lesson_summary,
                    labels=None,
                    created_at=learning.created_at,
                )
                db.add(row)
            else:
                row.outcome = learning.insight
                row.pnl_pct = learning.pnl_pct
                row.lesson_summary = learning.lesson_summary
            db.commit()

    # ---------- Alert ----------
    def save_alert(self, alert: Alert) -> None:
        _ensure_alert_table()
        with self._session_factory() as db:
            row = db.execute(select(orm.AlertModel).where(orm.AlertModel.alert_id == alert.alert_id)).scalar_one_or_none()
            if row is None:
                row = orm.AlertModel(
                    alert_id=alert.alert_id,
                    level=str(alert.level),
                    title=alert.title,
                    message=alert.message,
                    status=str(alert.status),
                    created_at=alert.created_at,
                    acked_at=alert.acked_at,
                )
                db.add(row)
            else:
                row.level = str(alert.level)
                row.title = alert.title
                row.message = alert.message
                row.status = str(alert.status)
                row.acked_at = alert.acked_at
            db.commit()

    def get_alert(self, alert_id: str) -> Alert | None:
        _ensure_alert_table()
        with self._session_factory() as db:
            row = db.execute(select(orm.AlertModel).where(orm.AlertModel.alert_id == alert_id)).scalar_one_or_none()
            return _to_alert(row) if row is not None else None

    def list_alerts(self, status: AlertStatus | None = None) -> list[Alert]:
        _ensure_alert_table()
        with self._session_factory() as db:
            q = select(orm.AlertModel)
            if status is not None:
                q = q.where(orm.AlertModel.status == str(status))
            rows = db.execute(q.order_by(orm.AlertModel.created_at.desc())).scalars().all()
            return [_to_alert(r) for r in rows]
