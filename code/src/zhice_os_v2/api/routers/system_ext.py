"""系统运维扩展 API（V1 system 对齐：数据质量/任务/审计）。"""
from __future__ import annotations

import logging
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Date as SaDate
from sqlalchemy import cast, func

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system-ext"], dependencies=[Depends(get_current_user)])

# 每交易日固定 4 个任务（V1 workflow_run 对齐）：任务名 → (阶段, 截止时刻)
_TASK_DEFS = [
    ("盘前工作流", "盘前", time(9, 30)),
    ("盘中快照", "盘中", time(10, 0)),
    ("盘后复盘", "盘后", time(15, 30)),
    ("学习汇总", "学习", time(16, 0)),
]


def _market_now(market: str) -> datetime:
    try:
        from zhice_os_v2.domain.market.registry import get_profile

        return datetime.now(get_profile(market).tz)
    except Exception:
        return datetime.now()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/continuity")
def continuity(market: str = "CN", days: int = 30, symbol: str | None = None):
    """日线连续性缺口汇总（基准=交易日历 is_open；近 days 日窗口）。"""
    from datetime import date as _date
    from datetime import timedelta

    from zhice_os_v2.infrastructure.market_data.continuity import (
        find_daily_gaps,
        summarize_gaps,
    )

    end = _date.today()
    start = end - timedelta(days=days)
    syms = [symbol] if symbol else None
    try:
        gaps = find_daily_gaps(market, start, end, symbols=syms)
        s = summarize_gaps(gaps, top=50)
    except Exception as exc:  # noqa: BLE001 - DB/查询异常按依赖不可用返回 503，不裸 500
        logger.exception("system/continuity 查询失败 market=%s days=%s", market, days)
        raise HTTPException(
            status_code=503,
            detail={"code": "system.continuity_unavailable"},
        ) from exc
    return {
        "market": market, "start": start.isoformat(), "end": end.isoformat(),
        "symbols_with_gaps": s["symbols_with_gaps"], "total_missing": s["total_missing"],
        "top": s["top"],
    }


@router.get("/data-quality")
def data_quality(market: str = "CN", date: str | None = None):
    """数据质量检查（V1 四类坏数据标记）。

    检查该市场最新交易日（date 缺省）全部日K：
    - missing_close: close IS NULL 或 <=0
    - zero_volume: volume IS NULL 或 =0
    - abnormal_change: pre_close 非空且 |close/pre_close-1| > 0.21（CRYPTO 放宽 0.5）
    - stale: 该日无日K的 instrument 数（total - covered）
    quality_score = (checked - 问题数) / checked * 100（clamped 0-100）。
    """
    threshold = 0.5 if market == "CRYPTO" else 0.21
    session = get_session()
    try:
        q = session.query(orm.BarDailyModel).filter(orm.BarDailyModel.market == market)
        if date:
            trade_date = date
        else:
            trade_date = (
                session.query(func.max(orm.BarDailyModel.trade_date))
                .filter(orm.BarDailyModel.market == market)
                .scalar()
            )
        if trade_date is None:
            return {
                "market": market,
                "trade_date": None,
                "total_instruments": 0,
                "checked": 0,
                "issues": {"missing_close": 0, "zero_volume": 0, "abnormal_change": 0, "stale": 0},
                "quality_score": 100.0,
            }
        q = q.filter(orm.BarDailyModel.trade_date == trade_date)

        total_instruments = (
            session.query(func.count(orm.InstrumentModel.id))
            .filter(orm.InstrumentModel.market == market)
            .scalar()
        ) or 0

        missing_close = q.filter(
            (orm.BarDailyModel.close.is_(None)) | (orm.BarDailyModel.close <= 0)
        ).count()
        zero_volume = q.filter(
            (orm.BarDailyModel.volume.is_(None)) | (orm.BarDailyModel.volume == 0)
        ).count()
        change = func.abs(orm.BarDailyModel.close / orm.BarDailyModel.pre_close - 1)
        abnormal = q.filter(
            orm.BarDailyModel.pre_close.isnot(None),
            orm.BarDailyModel.pre_close > 0,
            change > threshold,
        ).count()
        checked = q.count()
    finally:
        session.close()

    stale = max(total_instruments - checked, 0)
    issues = {
        "missing_close": missing_close,
        "zero_volume": zero_volume,
        "abnormal_change": abnormal,
        "stale": stale,
    }
    if checked > 0:
        score = (checked - (missing_close + zero_volume + abnormal + stale)) / checked * 100
        score = max(0.0, min(100.0, score))
    else:
        score = 0.0

    return {
        "market": market,
        "trade_date": str(trade_date) if trade_date else None,
        "total_instruments": int(total_instruments),
        "checked": int(checked),
        "issues": issues,
        "quality_score": round(score, 1),
    }


@router.get("/tasks")
def system_tasks(market: str = "CN", limit: int = 50):
    """任务状态（V1 workflow_run 对齐）。

    V2 无任务运行表——取 v2_market_calendar 最近 5 个交易日（无日历时回退
    bar_daily 交易日），结合当前时间推断每日 4 个任务的完成状态：
    - 盘前：该日 candidate_pool 存在 → 已完成
    - 盘中：该日 position opened → 已完成
    - 盘后：该日 replay close_date 存在 → 已完成
    - 学习：该日 learning_case created_at 存在 → 已完成
    """
    now = _market_now(market)
    today = now.date()

    session = get_session()
    try:
        dates = [
            row[0]
            for row in (
                session.query(orm.MarketCalendarModel.trade_date)
                .filter(
                    orm.MarketCalendarModel.market == market,
                    orm.MarketCalendarModel.is_open.is_(True),
                    orm.MarketCalendarModel.trade_date <= today,
                )
                .order_by(orm.MarketCalendarModel.trade_date.desc())
                .limit(5)
                .all()
            )
        ]
        if not dates:
            dates = [
                row[0]
                for row in (
                    session.query(orm.BarDailyModel.trade_date)
                    .filter(
                        orm.BarDailyModel.market == market,
                        orm.BarDailyModel.trade_date <= today,
                    )
                    .distinct()
                    .order_by(orm.BarDailyModel.trade_date.desc())
                    .limit(5)
                    .all()
                )
            ]

        tasks: list[dict] = []
        pool_counts: dict[date, int] = {}
        pos_counts: dict[date, int] = {}
        replay_counts: dict[date | None, int] = {}
        learn_counts: dict[date | None, int] = {}
        cand_counts: dict = {}
        close_counts: dict = {}
        minute_dates: set = set()
        if dates:
            pool_counts = {
                r[0]: r[1]
                for r in session.query(orm.CandidatePoolModel.pool_date, func.count(orm.CandidatePoolModel.id))
                .filter(
                    orm.CandidatePoolModel.market == market,
                    orm.CandidatePoolModel.pool_date.in_(dates),
                )
                .group_by(orm.CandidatePoolModel.pool_date)
                .all()
            }
            pos_counts = {
                r[0]: r[1]
                for r in session.query(
                    cast(orm.PositionModel.opened_at, SaDate).label("d"),
                    func.count(orm.PositionModel.id),
                )
                .filter(
                    orm.PositionModel.market == market,
                    cast(orm.PositionModel.opened_at, SaDate).in_(dates),
                )
                .group_by("d")
                .all()
            }
            replay_counts = {
                r[0]: r[1]
                for r in session.query(orm.ReplayModel.close_date, func.count(orm.ReplayModel.id))
                .filter(
                    orm.ReplayModel.market == market,
                    orm.ReplayModel.close_date.in_(dates),
                )
                .group_by(orm.ReplayModel.close_date)
                .all()
            }
            # 学习按其复盘的交易日(close_date)归属，与盘后复盘同口径（learning_case.created_at
            # 是生成时刻，回填时会是当天，无法反映历史交易日）
            learn_counts = {
                r[0]: r[1]
                for r in session.query(orm.ReplayModel.close_date, func.count(orm.LearningCaseModel.id))
                .join(
                    orm.ReplayModel,
                    orm.LearningCaseModel.replay_id == orm.ReplayModel.replay_id,
                )
                .filter(
                    orm.LearningCaseModel.market == market,
                    orm.ReplayModel.close_date.in_(dates),
                )
                .group_by(orm.ReplayModel.close_date)
                .all()
            }
            # 盘前兜底：候选资产（pool 为空但有候选也算盘前跑过）
            cand_counts = {
                r[0]: r[1]
                for r in session.query(
                    cast(orm.CandidateAssetModel.created_at, SaDate).label("d"),
                    func.count(orm.CandidateAssetModel.id),
                )
                .filter(
                    orm.CandidateAssetModel.market == market,
                    cast(orm.CandidateAssetModel.created_at, SaDate).in_(dates),
                )
                .group_by("d")
                .all()
            }
            # 当日平仓数：用于判定盘后/学习是否"有活可干"（无平仓则无需复盘/学习）
            close_counts = {
                r[0]: r[1]
                for r in session.query(
                    cast(orm.PositionModel.closed_at, SaDate).label("d"),
                    func.count(orm.PositionModel.id),
                )
                .filter(
                    orm.PositionModel.market == market,
                    cast(orm.PositionModel.closed_at, SaDate).in_(dates),
                )
                .group_by("d")
                .all()
            }
            # 当日是否采到分钟线：盘中采集"跑过"的历史可靠信号（快照是覆盖式不可用于历史）
            minute_dates = {
                r[0]
                for r in session.query(orm.BarMinuteModel.trade_date)
                .filter(
                    orm.BarMinuteModel.market == market,
                    orm.BarMinuteModel.trade_date.in_(dates),
                )
                .distinct()
                .all()
            }
    finally:
        session.close()

    def _decide(done: bool, cutoff, done_detail: str, d,
                skip: bool = False, skip_detail: str = "") -> tuple[str, str, bool]:
        """→ (status, detail, inferred)。inferred=True 表示 COMPLETED 是按数据
        存在性反推的（V2 无任务运行表；done=有产出痕迹、skip=无活可干），
        MISSING/PENDING 按截止时刻推断不属于该口径。前端据此显示「已完成（推断）」。"""
        if done:
            return "COMPLETED", done_detail, True
        if skip:  # 当日无活可干（如无平仓→无需复盘/学习），不算缺失
            return "COMPLETED", skip_detail, True
        if d < today or (d == today and now.time() >= cutoff):
            return "MISSING", f"已过 {cutoff.strftime('%H:%M')}，无数据", False
        return "PENDING", f"计划 {cutoff.strftime('%H:%M')} 执行", False

    for d in dates:  # dates 已按最近在前排序
        pool = pool_counts.get(d, 0)
        cand = cand_counts.get(d, 0)
        opened = pos_counts.get(d, 0)
        reps = replay_counts.get(d, 0)
        lrns = learn_counts.get(d, 0)
        closes = close_counts.get(d, 0)
        has_min = d in minute_dates
        no_close = closes == 0
        per = [
            _decide(pool > 0 or cand > 0, _TASK_DEFS[0][2],
                    f"候选池 {pool} 批 / 候选 {cand} 只", d),
            _decide(has_min or opened > 0, _TASK_DEFS[1][2],
                    f"分钟线 {'已采' if has_min else '无'} · 开仓 {opened} 笔", d),
            _decide(reps > 0, _TASK_DEFS[2][2], f"复盘 {reps} 笔", d,
                    skip=no_close, skip_detail="当日无平仓，无需复盘"),
            _decide(lrns > 0, _TASK_DEFS[3][2], f"学习案例 {lrns} 条", d,
                    skip=no_close, skip_detail="当日无平仓，无需学习"),
        ]
        for idx, (name, phase, _cutoff) in enumerate(_TASK_DEFS):
            status, detail, inferred = per[idx]
            tasks.append(
                {
                    "task_name": name,
                    "trade_date": d.isoformat(),
                    "phase": phase,
                    "status": status,
                    "detail": detail,
                    "inferred": inferred,
                }
            )

    return tasks[:limit]


@router.get("/audit/recent")
def audit_recent(limit: int = 30, _guard=Depends(deny_viewer)):
    """最近审计事件流：v2_risk_decision（SYSTEM 系统动作）+ v2_approval_request（USER 人工动作）。

    VIEWER 403（deny_viewer，与 audit/trace 同口径）：事件含成交明细（标的/方向/数量/决策人）。
    """
    session = get_session()
    try:
        decisions = (
            session.query(
                orm.RiskDecisionModel.created_at,
                orm.RiskDecisionModel.decision_result,
                orm.RiskDecisionModel.approver_type,
                orm.TradePlanModel.ts_code,
                orm.TradePlanModel.plan_id,
            )
            .join(orm.RiskCheckModel, orm.RiskCheckModel.check_id == orm.RiskDecisionModel.check_id)
            .join(orm.TradePlanModel, orm.TradePlanModel.plan_id == orm.RiskCheckModel.plan_id)
            .order_by(orm.RiskDecisionModel.created_at.desc())
            .limit(limit)
            .all()
        )
        approvals = (
            session.query(
                orm.ApprovalRequestModel.created_at,
                orm.ApprovalRequestModel.ts_code,
                orm.ApprovalRequestModel.direction,
                orm.ApprovalRequestModel.qty,
                orm.ApprovalRequestModel.status,
                orm.ApprovalRequestModel.decided_by,
            )
            .order_by(orm.ApprovalRequestModel.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()

    events = []
    for created_at, result, approver, ts_code, plan_id in decisions:
        events.append(
            {
                "time": _iso(created_at),
                "actor": approver or "SYSTEM",
                "action": "风控决策",
                "target": ts_code,
                "detail": f"{result} · {plan_id}",
            }
        )
    for created_at, ts_code, direction, qty, status, decided_by in approvals:
        qty_txt = f"{float(qty):g}" if qty is not None else "—"
        detail = f"{direction} {qty_txt} · {status}"
        if decided_by:
            detail += f" · {decided_by}"
        events.append(
            {
                "time": _iso(created_at),
                "actor": "USER",
                "action": "交易授权",
                "target": ts_code,
                "detail": detail,
            }
        )

    events.sort(key=lambda e: e["time"] or "", reverse=True)
    return events[:limit]


@router.get("/audit/trace/{trace_id}")
def audit_trace(trace_id: str, _guard=Depends(deny_viewer)):
    """Trace 全链路查询：按 v2_trade_plan.trace_id 串起 候选→计划→风控→订单→成交。无匹配返回 []。"""
    session = get_session()
    try:
        plans = (
            session.query(orm.TradePlanModel)
            .filter(orm.TradePlanModel.trace_id == trace_id)
            .all()
        )
        if not plans:
            return []

        plan_ids = [p.plan_id for p in plans]
        events: list[dict] = []

        # 候选
        candidates = (
            session.query(orm.CandidateAssetModel)
            .filter(orm.CandidateAssetModel.candidate_id.in_([p.candidate_id for p in plans]))
            .all()
        )
        for c in candidates:
            score = f"评分 {float(c.candidate_score):.3f}" if c.candidate_score is not None else "无评分"
            events.append(
                {
                    "stage": "候选",
                    "ts_code": c.ts_code,
                    "detail": f"入池 · {score}",
                    "time": _iso(c.created_at),
                }
            )

        # 计划
        for p in plans:
            events.append(
                {
                    "stage": "计划",
                    "ts_code": p.ts_code,
                    "detail": f"生成计划 {p.plan_id} · {p.plan_status}",
                    "time": _iso(p.created_at),
                }
            )

        # 风控（check + decision）
        decisions = (
            session.query(
                orm.RiskDecisionModel.created_at,
                orm.RiskDecisionModel.decision_result,
                orm.RiskDecisionModel.approver_type,
                orm.RiskCheckModel.check_stage,
                orm.TradePlanModel.ts_code,
            )
            .join(orm.RiskCheckModel, orm.RiskCheckModel.check_id == orm.RiskDecisionModel.check_id)
            .join(orm.TradePlanModel, orm.TradePlanModel.plan_id == orm.RiskCheckModel.plan_id)
            .filter(orm.RiskCheckModel.plan_id.in_(plan_ids))
            .all()
        )
        for created_at, result, approver, stage, ts_code in decisions:
            events.append(
                {
                    "stage": "风控",
                    "ts_code": ts_code,
                    "detail": f"{result} · {stage} · {approver or 'SYSTEM'}",
                    "time": _iso(created_at),
                }
            )

        # 订单（intent 可经 plan_id 或同 trace_id 关联）
        intents = (
            session.query(orm.OrderIntentModel)
            .filter(
                (orm.OrderIntentModel.plan_id.in_(plan_ids))
                | (orm.OrderIntentModel.trace_id == trace_id)
            )
            .all()
        )
        for i in intents:
            qty = f"{float(i.intent_qty):g}" if i.intent_qty is not None else "—"
            price = f"@{float(i.intent_price):.2f}" if i.intent_price is not None else ""
            events.append(
                {
                    "stage": "订单",
                    "ts_code": i.ts_code,
                    "detail": f"{i.intent_type} {i.intent_side} {qty} {price} · {i.status}",
                    "time": _iso(i.created_at),
                }
            )

        # 成交（intent → order_request → execution → fill）
        if intents:
            intent_ids = [i.intent_id for i in intents]
            fills = (
                session.query(
                    orm.FillRecordModel.fill_time,
                    orm.FillRecordModel.created_at,
                    orm.FillRecordModel.ts_code,
                    orm.FillRecordModel.fill_price,
                    orm.FillRecordModel.fill_qty,
                    orm.ExecutionRecordModel.execution_status,
                )
                .join(
                    orm.ExecutionRecordModel,
                    orm.ExecutionRecordModel.execution_id == orm.FillRecordModel.execution_id,
                )
                .join(
                    orm.OrderRequestModel,
                    orm.OrderRequestModel.request_id == orm.ExecutionRecordModel.request_id,
                )
                .filter(orm.OrderRequestModel.intent_id.in_(intent_ids))
                .all()
            )
            for fill_time, created_at, ts_code, price, qty, exec_status in fills:
                p_txt = f"@{float(price):.2f}" if price is not None else ""
                q_txt = f"{float(qty):g}" if qty is not None else "—"
                events.append(
                    {
                        "stage": "成交",
                        "ts_code": ts_code,
                        "detail": f"成交 {q_txt} {p_txt} · {exec_status}",
                        "time": _iso(fill_time or created_at),
                    }
                )
    finally:
        session.close()

    events.sort(key=lambda e: e["time"] or "")
    return events
