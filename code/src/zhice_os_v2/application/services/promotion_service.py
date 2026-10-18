"""策略晋升/降级/回滚服务（V1 promotion_service + V2 半自动语义）。

V1 阈值（调优后值）：
  DRAFT→STAGING: ≥30 样本 胜率≥50%（或人工直批）
  STAGING→PROD:  ≥100 样本 胜率≥55% 盈亏比≥1.5 且 OOS 门（walk-forward
                 oos_consistency≥0.6；V2 阶段5 前无 OOS 数据 → DEFER 缺 OOS，
                 允许人工 override）
  PROD 连亏 3 笔 LearningCase → 建议降级 STAGING

V2 半自动（用户确认）：每日评估 job 只写 PENDING 决策留痕，不直接改状态；
人工经 governance API 确认(CONFIRM) 后同事务执行状态变更+留痕。回滚随时人工。
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

STAGING_MIN_SAMPLES = 30
STAGING_MIN_WIN_RATE = 0.50
PROD_MIN_SAMPLES = 100
PROD_MIN_WIN_RATE = 0.55
PROD_MIN_PNL_RATIO = 1.5
DEMOTION_CONSECUTIVE_LOSS = 3

_ROLLBACK_MAP = {"PROD": "STAGING", "STAGING": "DRAFT", "ACTIVE": "STAGING"}


def _compute_stats(cases) -> dict:
    wins = [float(c.pnl_pct or 0) for c in cases if str(c.outcome) == "WIN"]
    losses = [abs(float(c.pnl_pct or 0)) for c in cases if str(c.outcome) == "LOSS"]
    n = len(cases)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 1.0
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
    return {"samples": n, "win_rate": round(win_rate, 4),
            "pnl_ratio": round(pnl_ratio, 4)}


def _latest_oos(sv: str) -> dict | None:
    """最近 walk-forward 回测的 OOS 指标（v2_backtest_run，阶段5 产出）。"""
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        if not hasattr(orm, "BacktestRunModel"):
            return None
        session = get_session()
        try:
            rows = (
                session.query(orm.BacktestRunModel)
                .filter(orm.BacktestRunModel.strategy_version_id == sv)
                .order_by(orm.BacktestRunModel.created_at.desc())
                .all()
            )
            for r in rows:
                params = (r.params or {}) if hasattr(r, "params") else {}
                if params.get("walk_forward"):
                    return params
            return None
        finally:
            session.close()
    except Exception:
        return None


def evaluate_promotion(sv: str, manual: bool = False) -> dict:
    """评估晋升资格（只读）。返回 {current_status, can_promote, target, reason, stats}。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        ver = (
            session.query(orm.StrategyVersionModel)
            .filter(orm.StrategyVersionModel.strategy_version_id == sv)
            .one_or_none()
        )
        if ver is None:
            return {"error": "version_not_found"}
        status = str(ver.status)
        cases = (
            session.query(orm.LearningCaseModel)
            .filter(orm.LearningCaseModel.strategy_version_id == sv)
            .order_by(orm.LearningCaseModel.created_at.desc())
            .limit(500)
            .all()
        )
        stats = _compute_stats(cases)
        target = {"DRAFT": "STAGING", "STAGING": "PROD", "PENDING_REVIEW": "STAGING"}.get(status)
        if target is None:
            return {"current_status": status, "can_promote": False, "target": None,
                    "reason": f"{status} 无晋升路径", "stats": stats}
        if manual:
            return {"current_status": status, "can_promote": True, "target": target,
                    "reason": "人工直批", "stats": stats}
        if target == "STAGING":
            ok = stats["samples"] >= STAGING_MIN_SAMPLES and stats["win_rate"] >= STAGING_MIN_WIN_RATE
            reason = (f"样本{stats['samples']}/胜率{stats['win_rate']:.0%} "
                      f"(门槛 {STAGING_MIN_SAMPLES}/{STAGING_MIN_WIN_RATE:.0%})")
        else:
            live_ok = (stats["samples"] < PROD_MIN_SAMPLES
                       or (stats["win_rate"] >= PROD_MIN_WIN_RATE
                           and stats["pnl_ratio"] >= PROD_MIN_PNL_RATIO))
            oos = _latest_oos(sv)
            oos_ok = oos is not None and float(oos.get("oos_consistency", 0)) >= 0.6
            if not live_ok:
                return {"current_status": status, "can_promote": False, "target": target,
                        "reason": f"实盘指标未达标 样本{stats['samples']} 胜率{stats['win_rate']:.0%} "
                                  f"盈亏比{stats['pnl_ratio']}", "stats": stats}
            if oos is None:
                return {"current_status": status, "can_promote": False, "target": target,
                        "reason": "DEFER：实盘达标但无 walk-forward OOS 记录（可人工 override）",
                        "stats": stats, "defer": "no_oos"}
            ok = oos_ok
            reason = f"OOS 一致性 {oos.get('oos_consistency')}"
        return {"current_status": status, "can_promote": ok, "target": target,
                "reason": reason, "stats": stats}
    finally:
        session.close()


def check_demotion(sv: str) -> bool:
    """PROD 连亏 3 笔 → 建议降级。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        ver = (
            session.query(orm.StrategyVersionModel.status)
            .filter(orm.StrategyVersionModel.strategy_version_id == sv)
            .first()
        )
        if not ver or str(ver[0]) not in ("PROD", "ACTIVE"):
            return False
        last3 = (
            session.query(orm.LearningCaseModel.outcome)
            .filter(orm.LearningCaseModel.strategy_version_id == sv)
            .order_by(orm.LearningCaseModel.created_at.desc())
            .limit(DEMOTION_CONSECUTIVE_LOSS)
            .all()
        )
        return len(last3) >= DEMOTION_CONSECUTIVE_LOSS and \
            all(str(r[0]) == "LOSS" for r in last3)
    finally:
        session.close()


def record_decision(sv: str, decision: str, from_status: str | None, to_status: str | None,
                    reason: str, stats: dict | None = None, experiment_run_id: str | None = None,
                    approver_type: str = "SYSTEM", confirm_status: str = "PENDING") -> str:
    """写决策留痕（幂等：decision_id 唯一）。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    now = utc_now()
    did = f"pd-{sv}-{decision}-{now.strftime('%Y%m%d%H%M')}"
    stats = stats or {}
    session = get_session()
    try:
        session.execute(pg_insert(orm.PromotionDecisionModel).values(
            decision_id=did, strategy_version_id=sv, decision=decision,
            from_status=from_status, to_status=to_status, reason=reason,
            samples=int(stats.get("samples", 0)),
            win_rate=Decimal(str(stats.get("win_rate", 0))) if stats.get("win_rate") is not None else None,
            pnl_ratio=Decimal(str(stats.get("pnl_ratio", 0))) if stats.get("pnl_ratio") is not None else None,
            approver_type=approver_type, experiment_run_id=experiment_run_id,
            confirm_status=confirm_status, created_at=now,
        ).on_conflict_do_nothing(index_elements=["decision_id"]))
        session.commit()
        return did
    finally:
        session.close()


def confirm_decision(decision_id: str, actor: str = "manual") -> dict:
    """人工确认 PENDING 决策 → 执行状态变更（同事务留痕）。"""
    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        d = (
            session.query(orm.PromotionDecisionModel)
            .filter(orm.PromotionDecisionModel.decision_id == decision_id)
            .one_or_none()
        )
        if d is None:
            return {"error": "not_found"}
        if d.confirm_status != "PENDING":
            return {"error": f"already_{d.confirm_status.lower()}"}
        if d.decision in ("PROMOTE", "ROLLBACK") and d.to_status:
            ver = (
                session.query(orm.StrategyVersionModel)
                .filter(orm.StrategyVersionModel.strategy_version_id == d.strategy_version_id)
                .one_or_none()
            )
            if ver is None:
                return {"error": "version_not_found"}
            ver.status = d.to_status
        d.confirm_status = "CONFIRMED"
        d.approver_type = "MANUAL"
        d.decided_at = utc_now()
        session.commit()
        return {"decision_id": decision_id, "executed": True,
                "to_status": d.to_status, "actor": actor}
    finally:
        session.close()


def run_promotion_check() -> dict:
    """每日评估 job：全版本评估 → 达标写 PENDING 决策（不改状态）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        svs = [r[0] for r in (
            session.query(orm.StrategyVersionModel.strategy_version_id)
            .filter(orm.StrategyVersionModel.status.in_(("DRAFT", "STAGING", "ACTIVE", "PROD")))
            .all()
        )]
    finally:
        session.close()
    decisions = []
    for sv in svs:
        try:
            if check_demotion(sv):
                st = evaluate_promotion(sv)
                did = record_decision(sv, "ROLLBACK", st.get("current_status"), "STAGING",
                                      f"PROD 连亏 {DEMOTION_CONSECUTIVE_LOSS} 笔，建议降级")
                decisions.append({"sv": sv, "decision": "ROLLBACK", "decision_id": did})
                continue
            r = evaluate_promotion(sv)
            if r.get("can_promote"):
                did = record_decision(sv, "PROMOTE", r["current_status"], r["target"],
                                      r["reason"], r.get("stats"))
                decisions.append({"sv": sv, "decision": "PROMOTE", "decision_id": did})
            elif r.get("defer"):
                did = record_decision(sv, "DEFER", r["current_status"], r["target"], r["reason"])
                decisions.append({"sv": sv, "decision": "DEFER", "decision_id": did})
        except Exception:
            logger.exception("promotion_check failed sv=%s", sv)
    result = {"checked": len(svs), "decisions": decisions}
    logger.info("promotion_check %s", result)
    return result


# ---------------- 灰度发布 ----------------

GRAY_MIN_SAMPLES = 20
_DEFAULT_THRESHOLD = {"min_win_rate": 0.55, "min_pnl_ratio": 1.0}


def start_gray_release(sv: str, scope: str = "10%仓位上限", observation_days: int = 14) -> dict:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        active = (
            session.query(orm.GrayReleaseModel)
            .filter(orm.GrayReleaseModel.strategy_version_id == sv,
                    orm.GrayReleaseModel.status == "ACTIVE")
            .first()
        )
        if active:
            return {"error": "active_exists", "gray_release_id": active.gray_release_id}
        gid = f"gray-{sv}-{utc_now().strftime('%Y%m%d%H%M')}"
        session.execute(pg_insert(orm.GrayReleaseModel).values(
            gray_release_id=gid, strategy_version_id=sv, scope=scope,
            observation_days=observation_days, metrics_threshold=_DEFAULT_THRESHOLD,
            status="ACTIVE", started_at=utc_now(),
        ).on_conflict_do_nothing(index_elements=["gray_release_id"]))
        session.commit()
        return {"gray_release_id": gid, "status": "ACTIVE"}
    finally:
        session.close()


def conclude_gray_release(gid: str, conclusion: str, request_promotion: bool = False) -> dict:
    """结题：观测窗样本≥20 且 胜率≥0.55 且 盈亏比≥1.0 → passed（半自动：仅建议）。"""
    from datetime import timedelta

    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        g = (
            session.query(orm.GrayReleaseModel)
            .filter(orm.GrayReleaseModel.gray_release_id == gid)
            .one_or_none()
        )
        if g is None or g.status != "ACTIVE":
            return {"error": "not_active"}
        window_end = g.started_at + timedelta(days=g.observation_days)
        lots = (
            session.query(orm.PositionModel)
            .filter(orm.PositionModel.strategy_version_id == g.strategy_version_id,
                    orm.PositionModel.status == "CLOSED",
                    orm.PositionModel.opened_at >= g.started_at,
                    orm.PositionModel.closed_at <= (window_end if utc_now() > window_end else utc_now()))
            .all()
        )
        wins = [l for l in lots if float(l.pnl_amount or 0) > 0]
        stats = {"samples": len(lots),
                 "win_rate": round(len(wins) / len(lots), 4) if lots else 0.0}
        passed = (stats["samples"] >= GRAY_MIN_SAMPLES
                  and stats["win_rate"] >= _DEFAULT_THRESHOLD["min_win_rate"])
        g.status = "CONCLUDED"
        g.observed_metrics = stats
        g.conclusion = conclusion
        g.concluded_at = utc_now()
        session.commit()
        promo = None
        if passed and request_promotion:
            r = evaluate_promotion(g.strategy_version_id)
            if r.get("can_promote"):
                promo = record_decision(g.strategy_version_id, "PROMOTE",
                                        r["current_status"], r["target"], r["reason"], r.get("stats"))
        return {"gray_release_id": gid, "passed": passed, "metrics": stats, "promotion_decision": promo}
    finally:
        session.close()
