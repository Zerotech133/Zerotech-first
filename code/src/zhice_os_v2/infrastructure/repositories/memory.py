"""内存版 Repository，用于 V2 早期可运行骨架与测试。

后续会替换为 SQLAlchemy/PostgreSQL 实现，但接口保持一致。
"""
from __future__ import annotations

from collections import defaultdict

from zhice_os_v2.domain.alert.alert import Alert, AlertStatus
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.candidate.candidate import Candidate, CandidatePool
from zhice_os_v2.domain.instrument.instrument import Instrument
from zhice_os_v2.domain.learning.learning import LearningCase
from zhice_os_v2.domain.planning.trade_plan import PlanStatus, TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.replay.replay import ReplayCase
from zhice_os_v2.domain.strategy.strategy import (
    Strategy,
    StrategyPublicationRequest,
    StrategyVersion,
)


class InMemoryDatabase:
    def __init__(self) -> None:
        self.strategies: dict[str, Strategy] = {}
        self.strategy_versions: dict[str, StrategyVersion] = {}
        self.publication_requests: dict[str, StrategyPublicationRequest] = {}
        self.candidate_pools: dict[str, CandidatePool] = {}
        self.candidates: dict[str, Candidate] = {}
        self.trade_plans: dict[str, TradePlan] = {}
        self.approvals: dict[str, ApprovalRequest] = {}
        self.positions: dict[str, PositionLot] = {}
        self.replays: dict[str, ReplayCase] = {}
        self.learnings: dict[str, LearningCase] = {}
        self.instruments: dict[str, Instrument] = {}
        self.alerts: dict[str, Alert] = {}

        # 简单索引：strategy_version_id -> ids
        self._by_sv: dict[str, set[str]] = defaultdict(set)

    # ---- helpers ----
    def _index(self, key: str, sv_id: str | None) -> None:
        if sv_id:
            self._by_sv[sv_id].add(key)

    # ---- Strategy ----
    def save_strategy(self, strategy: Strategy) -> None:
        self.strategies[strategy.strategy_id] = strategy

    def get_strategy(self, strategy_id: str) -> Strategy | None:
        return self.strategies.get(strategy_id)

    def list_strategies(self) -> list[Strategy]:
        return list(self.strategies.values())

    def save_strategy_version(self, version: StrategyVersion) -> None:
        self.strategy_versions[version.strategy_version_id] = version
        self._index(version.strategy_version_id, version.strategy_id)

    def get_strategy_version(self, version_id: str) -> StrategyVersion | None:
        return self.strategy_versions.get(version_id)

    def list_strategy_versions(self) -> list[StrategyVersion]:
        return list(self.strategy_versions.values())

    def is_version_referenced(self, strategy_version_id: str) -> bool:
        """该版本是否被交易痕迹引用（不可变守卫）。

        内存后端仅承载 TradePlan/Position/Replay（UserStrategyInstance 只在 SQL 仓储，
        内存库无对应集合，无法在此判定——SQL 实现覆盖该表）。任一命中即返回 True。
        """
        if any(
            p.strategy_version_id == strategy_version_id
            for p in self.trade_plans.values()
        ):
            return True
        if any(
            p.strategy_version_id == strategy_version_id
            for p in self.positions.values()
        ):
            return True
        return any(
            r.strategy_version_id == strategy_version_id for r in self.replays.values()
        )

    def delete_strategy(self, strategy_id: str) -> None:
        self.strategies.pop(strategy_id, None)
        for vid in [v.strategy_version_id for v in self.strategy_versions.values()
                    if v.strategy_id == strategy_id]:
            self.strategy_versions.pop(vid, None)

    def delete_strategy_version(self, version_id: str) -> None:
        self.strategy_versions.pop(version_id, None)

    # ---- Strategy publication request ----
    def save_publication_request(self, request: StrategyPublicationRequest) -> None:
        self.publication_requests[request.request_id] = request

    def get_publication_request(
        self, request_id: str
    ) -> StrategyPublicationRequest | None:
        return self.publication_requests.get(request_id)

    def get_pending_publication_request(
        self, source_strategy_id: str
    ) -> StrategyPublicationRequest | None:
        for req in self.publication_requests.values():
            if req.source_strategy_id == source_strategy_id and req.status == "PENDING":
                return req
        return None

    def _pre_commit_publication(self) -> None:
        """测试注入点占位（内存后端无真事务；原子性由单调用内无 I/O 保证）。"""

    def commit_publication_approval(
        self,
        *,
        derived_strategy: Strategy,
        derived_version: StrategyVersion,
        source_strategy_id: str,
        request: StrategyPublicationRequest,
    ) -> None:
        # 内存后端：单方法内无 I/O，全部赋值天然原子（先校验点，再一次性改）。
        self._pre_commit_publication()
        self.strategies[derived_strategy.strategy_id] = derived_strategy
        self.strategy_versions[derived_version.strategy_version_id] = derived_version
        self._index(derived_version.strategy_version_id, derived_version.strategy_id)
        source = self.strategies.get(source_strategy_id)
        if source is not None:
            source.publication_status = "PRIVATE"
        self.publication_requests[request.request_id] = request

    def commit_publication_rejection(
        self, *, source_strategy_id: str, request: StrategyPublicationRequest
    ) -> None:
        self._pre_commit_publication()
        source = self.strategies.get(source_strategy_id)
        if source is not None:
            source.publication_status = "PRIVATE"
        self.publication_requests[request.request_id] = request

    # ---- Candidate ----
    def save_candidate_pool(self, pool: CandidatePool) -> None:
        self.candidate_pools[pool.pool_id] = pool
        for c in pool.candidates:
            self.save_candidate(c)

    def save_candidate(self, candidate: Candidate) -> None:
        self.candidates[candidate.candidate_id] = candidate
        self._index(candidate.candidate_id, candidate.strategy_version_id)

    def list_candidates_by_strategy(self, strategy_version_id: str) -> list[Candidate]:
        return [self.candidates[i] for i in self._by_sv[strategy_version_id] if i in self.candidates]

    # ---- TradePlan ----
    def save_trade_plan(self, plan: TradePlan) -> None:
        existing = self.trade_plans.get(plan.plan_id)
        if existing is not None and existing.status in (
            PlanStatus.CANCELLED,
            PlanStatus.REJECTED,
            PlanStatus.EXECUTED,
            PlanStatus.EXPIRED,
        ):
            return  # Don't overwrite terminal-state plans
        self.trade_plans[plan.plan_id] = plan
        self._index(plan.plan_id, plan.strategy_version_id)

    def get_trade_plan(self, plan_id: str) -> TradePlan | None:
        return self.trade_plans.get(plan_id)

    def list_trade_plans(self) -> list[TradePlan]:
        return list(self.trade_plans.values())

    def update_trade_plan_status(self, plan_id: str, status: str) -> None:
        plan = self.trade_plans.get(plan_id)
        if plan is not None:
            from zhice_os_v2.domain.planning.trade_plan import PlanStatus
            plan.status = PlanStatus(status)

    def expire_stale_pending_plans(self, market: str, session_date) -> dict:
        """口径A 同会话过期：market 下 创建日<session_date 且未被批准 的 PENDING 计划
        置 EXPIRED，其 PENDING approval 一并 expire()。硬排除 approval=APPROVED，
        幂等（只动 PENDING）。返回 {plans_expired, approvals_expired}。
        """
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        from zhice_os_v2.domain.planning.trade_plan import PlanStatus

        approved_intents = {
            a.order_intent_id for a in self.approvals.values()
            if a.status == ApprovalStatus.APPROVED
        }
        expired_ids: list[str] = []
        for plan in self.trade_plans.values():
            if (plan.market == market
                    and plan.status == PlanStatus.PENDING
                    and plan.created_at.date() < session_date
                    and plan.plan_id not in approved_intents):
                plan.status = PlanStatus.EXPIRED
                expired_ids.append(plan.plan_id)
        expired_set = set(expired_ids)
        m = 0
        for a in self.approvals.values():
            if a.order_intent_id in expired_set and a.status == ApprovalStatus.PENDING:
                a.expire()
                m += 1
        return {"plans_expired": len(expired_ids), "approvals_expired": m}

    def expire_stale_active_approvals(self, market: str, session_date) -> dict:
        """日内单语义过期：market 下 created_at 日期<session_date 且无对应 OPEN 持仓 的
        非终态(PENDING/APPROVED/EXECUTING)承认置 EXPIRED，镜像其计划置 EXPIRED。

        根因（2026-09-02）：sim 自动批准的 APPROVED 若当日未约定则无终态收敛，逐日累积
        撑爆 sim_max_positions → pre_market 早退零计划。此扫描使 active 承认恒收敛到今日
        在途，并兜住计划已过期而承认孤儿滞留 PENDING 的历史残渣。硬保护：已落 OPEN 持仓
        的承认不动（防误爆在持仓）；终态(EXECUTED/EXPIRED/CANCELLED/REJECTED/
        PARTIALLY_EXECUTED)不动；今日在途不动。幂等。
        """
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        from zhice_os_v2.domain.ontology.objects import utc_now
        from zhice_os_v2.domain.planning.trade_plan import PlanStatus

        _ACTIVE = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.EXECUTING)
        held_plan_ids = {
            p.trade_plan_id for p in self.positions.values()
            if p.status.value == "OPEN" and p.trade_plan_id
        }
        n = 0
        expired_intents: list[str] = []
        for a in self.approvals.values():
            if (a.market == market
                    and a.status in _ACTIVE
                    and a.created_at.date() < session_date
                    and a.order_intent_id not in held_plan_ids):
                a.status = ApprovalStatus.EXPIRED
                a.decided_at = utc_now()
                expired_intents.append(a.order_intent_id)
                n += 1
        pm = 0
        expired_set = set(expired_intents)
        for plan in self.trade_plans.values():
            if (plan.plan_id in expired_set
                    and plan.status in (PlanStatus.PENDING, PlanStatus.APPROVED)):
                plan.status = PlanStatus.EXPIRED
                pm += 1
        return {"active_approvals_expired": n, "active_plans_expired": pm}

    # ---- Approval ----
    def save_approval(self, request: ApprovalRequest) -> None:
        existing = self.approvals.get(request.request_id)
        if existing is not None and existing.status in (
            ApprovalStatus.REJECTED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.EXECUTED,
            ApprovalStatus.EXECUTING,
            ApprovalStatus.PARTIALLY_EXECUTED,
        ):
            return  # Don't overwrite terminal-state approvals
        self.approvals[request.request_id] = request

    def get_approval(self, request_id: str) -> ApprovalRequest | None:
        return self.approvals.get(request_id)

    def list_approvals(self) -> list[ApprovalRequest]:
        return list(self.approvals.values())

    def claim_approval_for_execution(self, request_id: str) -> bool:
        """原子性地将 APPROVED → EXECUTING（内存版，单线程安全）。"""
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        req = self.approvals.get(request_id)
        if req is None or req.status != ApprovalStatus.APPROVED:
            return False
        req.status = ApprovalStatus.EXECUTING
        return True

    def release_approval_claim(self, request_id: str) -> bool:
        """claim 的逆操作：EXECUTING → APPROVED（fill 失败/异常时回滚执行占位）。"""
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        req = self.approvals.get(request_id)
        if req is None or req.status != ApprovalStatus.EXECUTING:
            return False
        req.status = ApprovalStatus.APPROVED
        return True

    def mark_approval_executed(self, request_id: str, status: str = "EXECUTED") -> bool:
        """成交完成终态：EXECUTING → EXECUTED（或 PARTIALLY_EXECUTED，超持仓卖出）。
        仅在 EXECUTING 态生效（幂等、防越权）。

        save_approval 把 EXECUTING 视为不可覆写终态，故 _auto_execute / 人工执行成交后无法经
        save_approval 落终态（全系统 0 个 EXECUTED、已成交承认永卡 EXECUTING 计入
        sim_max_positions）。此直改状态方法绕过守卫，为 claim 的正向收尾。
        """
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        from zhice_os_v2.domain.ontology.objects import utc_now
        req = self.approvals.get(request_id)
        if req is None or req.status != ApprovalStatus.EXECUTING:
            return False
        req.status = ApprovalStatus(status)
        req.decided_at = utc_now()
        return True

    # ---- Position ----
    def save_position(self, position: PositionLot) -> None:
        self.positions[position.position_id] = position
        self._index(position.position_id, position.strategy_version_id)

    def get_position(self, position_id: str) -> PositionLot | None:
        return self.positions.get(position_id)

    def list_positions(self, market: str | None = None) -> list[PositionLot]:
        positions = list(self.positions.values())
        if market:
            positions = [p for p in positions if p.market == market]
        return positions

    def list_open_positions(self, market: str | None = None) -> list[PositionLot]:
        positions = [p for p in self.positions.values() if p.status.value == "OPEN"]
        if market:
            positions = [p for p in positions if p.market == market]
        return positions

    # ---- Replay ----
    def save_replay(self, replay: ReplayCase) -> None:
        self.replays[replay.replay_id] = replay

    def list_replays(self) -> list[ReplayCase]:
        return list(self.replays.values())

    # ---- Learning ----
    def save_learning(self, learning: LearningCase) -> None:
        self.learnings[learning.learning_id] = learning

    # ---- Alert ----
    def save_alert(self, alert: Alert) -> None:
        self.alerts[alert.alert_id] = alert

    def get_alert(self, alert_id: str) -> Alert | None:
        return self.alerts.get(alert_id)

    def list_alerts(self, status: AlertStatus | None = None) -> list[Alert]:
        items = [a for a in self.alerts.values() if status is None or a.status == status]
        return sorted(items, key=lambda a: a.created_at, reverse=True)

    # ---- Instrument ----
    def save_instrument(self, instrument: Instrument) -> None:
        self.instruments[instrument.instrument_id] = instrument

    def get_instrument_by_symbol(self, market: str, symbol: str) -> Instrument | None:
        for inst in self.instruments.values():
            if inst.market == market and inst.symbol == symbol:
                return inst
        return None
