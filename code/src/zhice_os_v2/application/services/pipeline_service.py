"""交易闭环 Pipeline 聚合服务。

把候选/计划/风控/授权/持仓/复盘按 (market, ts_code) 缝成决策流。
全链均使用真实生产表（ORM 直查），不再从 approval 反推。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

_STAGES = ["candidate", "plan", "risk", "approval", "position", "replay"]


class PipelineService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 主查询：按 market 返回决策流列表
    # ------------------------------------------------------------------
    def decisions(self, market: str, strategy: str | None = None) -> list[dict]:
        session = get_session()
        try:
            data = self._load_all(session, market)
        finally:
            session.close()
        results = self._build_decisions(market, data)
        # 解析策略名；空/"0"/None 归属统一显示「未分配」（此前 UI 直接渲染原始 id，
        # 无归属计划显示成 "0"，看起来像 bug）
        from zhice_os_v2.application.services.account_stats_service import AccountStatsService
        names = AccountStatsService._strategy_names()
        for d in results:
            sv = d.get("strategy_version_id")
            d["strategy_name"] = "未分配" if (not sv or sv in ("0", "")) else names.get(sv, sv)
        if strategy:
            results = [d for d in results if d["strategy_version_id"] == strategy]
        return results

    # ------------------------------------------------------------------
    # 漏斗统计（由 decisions 派生，保证与决策流口径一致）
    # ------------------------------------------------------------------
    def funnel(self, market: str, strategy: str | None = None) -> dict:
        ds = self.decisions(market, strategy=strategy)
        return {
            "market": market,
            "candidate": sum(1 for d in ds if d["stages"]["candidate"]["done"]),
            "plan": sum(1 for d in ds if d["stages"]["plan"]["done"]),
            "approval_pending": sum(
                1 for d in ds if d["stages"]["approval"].get("status") == "PENDING"
            ),
            "position": sum(1 for d in ds if d["stages"]["position"]["done"]),
            "replay": sum(1 for d in ds if d["stages"]["replay"]["done"]),
        }

    # ------------------------------------------------------------------
    # 内部：批量加载所有表数据（避免 N+1）
    # ------------------------------------------------------------------
    def _load_all(self, session: Session, market: str) -> dict[str, Any]:
        # 1. Candidate assets: latest per ts_code (ORDER BY created_at DESC → first wins)
        candidates_raw = session.execute(
            select(orm.CandidateAssetModel)
            .where(orm.CandidateAssetModel.market == market)
            .order_by(
                orm.CandidateAssetModel.ts_code,
                orm.CandidateAssetModel.created_at.desc(),
            )
        ).scalars().all()
        cand_by_code: dict[str, orm.CandidateAssetModel] = {}
        for c in candidates_raw:
            if c.ts_code not in cand_by_code:
                cand_by_code[c.ts_code] = c

        # 2. Trade plans: latest per ts_code
        plans_raw = session.execute(
            select(orm.TradePlanModel)
            .where(orm.TradePlanModel.market == market)
            .order_by(
                orm.TradePlanModel.ts_code,
                orm.TradePlanModel.created_at.desc(),
            )
        ).scalars().all()
        plan_by_code: dict[str, orm.TradePlanModel] = {}
        plan_by_id: dict[str, orm.TradePlanModel] = {}
        for p in plans_raw:
            plan_by_id[p.plan_id] = p
            if p.ts_code not in plan_by_code:
                plan_by_code[p.ts_code] = p

        # 3. Risk chain: risk_check by plan_id → risk_decision by check_id
        all_plan_ids = list(plan_by_id.keys())
        risk_checks: list[orm.RiskCheckModel] = []
        if all_plan_ids:
            risk_checks = list(
                session.execute(
                    select(orm.RiskCheckModel).where(
                        orm.RiskCheckModel.market == market,
                        orm.RiskCheckModel.plan_id.in_(all_plan_ids),
                    )
                ).scalars().all()
            )

        check_by_plan: dict[str, list[orm.RiskCheckModel]] = defaultdict(list)
        for rc in risk_checks:
            check_by_plan[rc.plan_id].append(rc)

        all_check_ids = [rc.check_id for rc in risk_checks]
        risk_decisions: list[orm.RiskDecisionModel] = []
        if all_check_ids:
            risk_decisions = list(
                session.execute(
                    select(orm.RiskDecisionModel).where(
                        orm.RiskDecisionModel.market == market,
                        orm.RiskDecisionModel.check_id.in_(all_check_ids),
                    )
                ).scalars().all()
            )

        # Latest risk decision per check_id
        decision_by_check: dict[str, orm.RiskDecisionModel] = {}
        for rd in risk_decisions:
            if rd.check_id not in decision_by_check:
                decision_by_check[rd.check_id] = rd

        # Resolve: ts_code → risk decision_result
        risk_by_code: dict[str, str] = {}
        for ts_code, plan in plan_by_code.items():
            for rc in check_by_plan.get(plan.plan_id, []):
                if rc.check_id in decision_by_check:
                    risk_by_code[ts_code] = decision_by_check[rc.check_id].decision_result
                    break

        # 4. Approvals per ts_code
        approvals_raw = session.execute(
            select(orm.ApprovalRequestModel).where(
                orm.ApprovalRequestModel.market == market
            )
        ).scalars().all()
        apr_by_code: dict[str, list[orm.ApprovalRequestModel]] = defaultdict(list)
        for a in approvals_raw:
            apr_by_code[a.ts_code].append(a)

        # 5. Positions per ts_code (OPEN first, then others)
        positions_raw = session.execute(
            select(orm.PositionModel)
            .where(orm.PositionModel.market == market)
            .order_by(
                orm.PositionModel.ts_code,
                orm.PositionModel.opened_at.desc(),
            )
        ).scalars().all()
        pos_by_code: dict[str, list[orm.PositionModel]] = defaultdict(list)
        for pos_row in positions_raw:
            pos_by_code[pos_row.ts_code].append(pos_row)

        # 6. Replays: latest per ts_code
        replays_raw = session.execute(
            select(orm.ReplayModel)
            .where(orm.ReplayModel.market == market)
            .order_by(
                orm.ReplayModel.ts_code,
                orm.ReplayModel.created_at.desc(),
            )
        ).scalars().all()
        rep_by_code: dict[str, orm.ReplayModel] = {}
        for r in replays_raw:
            if r.ts_code not in rep_by_code:
                rep_by_code[r.ts_code] = r

        # 7. Attribution records per replay_id
        all_replay_ids = [r.replay_id for r in rep_by_code.values()]
        attr_records: list[orm.AttributionRecordModel] = []
        if all_replay_ids:
            attr_records = list(
                session.execute(
                    select(orm.AttributionRecordModel).where(
                        orm.AttributionRecordModel.market == market,
                        orm.AttributionRecordModel.replay_id.in_(all_replay_ids),
                    )
                ).scalars().all()
            )
        attr_by_replay: dict[str, list[orm.AttributionRecordModel]] = defaultdict(list)
        for ar in attr_records:
            attr_by_replay[ar.replay_id].append(ar)

        return {
            "cand_by_code": cand_by_code,
            "plan_by_code": plan_by_code,
            "risk_by_code": risk_by_code,
            "apr_by_code": apr_by_code,
            "pos_by_code": pos_by_code,
            "rep_by_code": rep_by_code,
            "attr_by_replay": attr_by_replay,
        }

    # ------------------------------------------------------------------
    # 内部：构建决策流列表
    # ------------------------------------------------------------------
    def _build_decisions(self, market: str, data: dict) -> list[dict]:
        cand_by_code = data["cand_by_code"]
        plan_by_code = data["plan_by_code"]
        risk_by_code = data["risk_by_code"]
        apr_by_code = data["apr_by_code"]
        pos_by_code = data["pos_by_code"]
        rep_by_code = data["rep_by_code"]
        attr_by_replay = data["attr_by_replay"]

        all_codes = (
            set(cand_by_code)
            | set(plan_by_code)
            | set(apr_by_code)
            | set(pos_by_code)
            | set(rep_by_code)
        )

        results: list[dict] = []
        for ts_code in all_codes:
            flow = self._build_flow(
                market,
                ts_code,
                cand_by_code.get(ts_code),
                plan_by_code.get(ts_code),
                risk_by_code.get(ts_code),
                list(apr_by_code.get(ts_code, [])),
                list(pos_by_code.get(ts_code, [])),
                rep_by_code.get(ts_code),
                attr_by_replay,
            )
            results.append(flow)

        # 排序：attention(PENDING 授权) 优先，然后有 replay 的按 pnl 降序
        results.sort(
            key=lambda d: (
                not d["attention"],
                -(d["stages"]["replay"]["pnl"] or 0.0),
            )
        )
        return results

    def _build_flow(
        self,
        market: str,
        ts_code: str,
        cand: orm.CandidateAssetModel | None,
        plan: orm.TradePlanModel | None,
        risk_result: str | None,
        apr_list: list[orm.ApprovalRequestModel],
        pos_list: list[orm.PositionModel],
        rep: orm.ReplayModel | None,
        attr_by_replay: dict[str, list[orm.AttributionRecordModel]],
    ) -> dict:
        # --- candidate ---
        candidate_done = cand is not None
        candidate_score = (
            float(cand.candidate_score)
            if cand and cand.candidate_score is not None
            else None
        )
        candidate_reason = cand.candidate_reason if cand else None

        # --- plan ---
        plan_done = plan is not None
        plan_id = plan.plan_id if plan else None
        ref_price = (
            float(plan.ref_price) if plan and plan.ref_price is not None else None
        )
        stop_loss = (
            float(plan.stop_loss_price)
            if plan and plan.stop_loss_price is not None
            else None
        )
        take_profit = (
            float(plan.take_profit_price)
            if plan and plan.take_profit_price is not None
            else None
        )

        # strategy_version_id: plan > approval > position > replay
        strategy_version_id: str | None = None
        if plan:
            strategy_version_id = plan.strategy_version_id
        elif apr_list:
            strategy_version_id = apr_list[0].strategy_version_id
        elif pos_list:
            strategy_version_id = getattr(pos_list[0], "strategy_version_id", None)
        elif rep:
            strategy_version_id = rep.strategy_version_id

        # --- risk ---
        risk_done = risk_result is not None

        # --- approval ---
        pending_aprs = [a for a in apr_list if a.status == "PENDING"]
        approved_aprs = [a for a in apr_list if a.status == "APPROVED"]
        _apr_candidates = pending_aprs or approved_aprs or apr_list
        best_apr: orm.ApprovalRequestModel | None = _apr_candidates[0] if _apr_candidates else None

        if best_apr is not None:
            approval_done = best_apr.status != "PENDING"
            approval_status: str | None = best_apr.status
            approval_request_id: str | None = best_apr.request_id
        else:
            # 有持仓/复盘但无 approval → V1 导入，视为已授权
            approval_done = bool(pos_list or rep)
            approval_status = "APPROVED" if approval_done else None
            approval_request_id = None

        # --- position ---
        open_positions = [p for p in pos_list if p.status == "OPEN"]
        _pos_candidates = open_positions or pos_list
        pos: orm.PositionModel | None = _pos_candidates[0] if _pos_candidates else None
        position_done = bool(pos_list)
        pos_qty = float(pos.open_qty) if pos else None
        pos_open_price = float(pos.open_price) if pos else None
        pos_pnl = (
            float(pos.pnl_amount) if pos and pos.pnl_amount is not None else None
        )
        pos_status = pos.status if pos else None

        # --- replay + attribution ---
        replay_done = rep is not None
        rep_pnl = float(rep.pnl) if rep else None
        rep_outcome = rep.outcome if rep else None
        rep_exit_reason = rep.exit_reason if rep else None
        attributions: list[dict] = []
        if rep:
            for ar in attr_by_replay.get(rep.replay_id, []):
                attributions.append(
                    {
                        "dimension": ar.dimension,
                        "weight": float(ar.weight) if ar.weight is not None else None,
                    }
                )

        # 最远完成阶段
        stages_done = {
            "candidate": candidate_done,
            "plan": plan_done,
            "risk": risk_done,
            "approval": approval_done,
            "position": position_done,
            "replay": replay_done,
        }
        current_stage = "candidate"
        for s in _STAGES:
            if stages_done[s]:
                current_stage = s

        attention = bool(pending_aprs)

        return {
            "ts_code": ts_code,
            "strategy_version_id": strategy_version_id,
            "market": market,
            "stage": current_stage,
            "stages": {
                "candidate": {
                    "done": candidate_done,
                    "score": candidate_score,
                    "reason": candidate_reason,
                },
                "plan": {
                    "done": plan_done,
                    "plan_id": plan_id,
                    "ref_price": ref_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                },
                "risk": {"done": risk_done, "result": risk_result},
                "approval": {
                    "done": approval_done,
                    "status": approval_status,
                    "request_id": approval_request_id,
                },
                "position": {
                    "done": position_done,
                    "qty": pos_qty,
                    "open_price": pos_open_price,
                    "pnl": pos_pnl,
                    "status": pos_status,
                },
                "replay": {
                    "done": replay_done,
                    "pnl": rep_pnl,
                    "outcome": rep_outcome,
                    "exit_reason": rep_exit_reason,
                    "attributions": attributions,
                },
            },
            "attention": attention,
        }
