"""治理看板 API（V1 governance 对齐）。

V1 参照 zhice/backend apps/workbench_api/routers/governance.py + GovernancePage.tsx：
  - 版本看板：三列分组（V1 PROD/STAGING/DRAFT → V2 ACTIVE/STAGING/其他）
  - 红线检查：V1 是可配置红线清单 + 单版本检查；V2 现算组合级检查结果
  - 错误趋势：按周×归因维度计数（V1 只统计 LOSS 负向 4 维，V2 数据含 6 维全量）
  - 非计划动作：V2 无 is_non_plan 列（psql 已确认），返回零值结构占位
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.api.routers.positions import _get_latest_prices
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/governance-ext", tags=["governance-ext"], dependencies=[Depends(get_current_user)])

# 6 个归因维度（v2_attribution_record 实际取值；V1 只统计负向 4 维，V2 全量展示）
ATTRIBUTION_DIMS = ["SELECTION", "ENTRY", "EXIT", "MARKET", "RISK", "BEHAVIOR"]

# 红线硬编码阈值（V1 红线为治理流程规则；V2 转为风控口径的现算检查）
SINGLE_SECURITY_POSITION_LIMIT = 20.0   # 单标的仓位上限 %
PORTFOLIO_POSITION_LIMIT = 80.0         # 组合仓位上限 %
DAILY_LOSS_LIMIT_PCT = 2.0              # 单日亏损上限（初始资金 %）


def _initial_capital(market: str) -> float:
    s = get_settings()
    return {
        "CN": s.sim_initial_capital,
        "US": s.sim_initial_capital_us,
        "HK": s.sim_initial_capital_hk,
        "CRYPTO": s.sim_initial_capital_crypto,
    }.get(market, s.sim_initial_capital)


def _clean_name(name: str | None) -> str:
    n = (name or "").strip()
    return n[5:] if n.startswith("[V1] ") else n


# ── 版本看板 ────────────────────────────────────────────────────────────────

@router.get("/versions-dashboard")
def versions_dashboard(market: str | None = None):
    """版本看板：三列分组（ACTIVE=生产/STAGING=预发/PENDING_REVIEW+其他=草稿区）。

    每版本 {strategy_version_id, strategy_name, market, version_no, status,
    position_count, unrealized_pnl}；position/pnl 从 v2_position OPEN 持仓聚合
    （浮盈用现价估算，缺价回退开仓价，与 AccountStatsService 同口径）。
    """
    session = get_session()
    try:
        q = (
            session.query(
                orm.StrategyVersionModel.strategy_version_id,
                orm.StrategyVersionModel.version_no,
                orm.StrategyVersionModel.market,
                orm.StrategyVersionModel.status,
                orm.StrategyModel.strategy_name,
            )
            .join(orm.StrategyModel, orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id)
        )
        if market:
            q = q.filter(orm.StrategyVersionModel.market == market)
        rows = q.order_by(orm.StrategyVersionModel.market, orm.StrategyVersionModel.version_no).all()

        lot_q = (
            session.query(
                orm.PositionModel.strategy_version_id,
                orm.PositionModel.ts_code,
                orm.PositionModel.market,
                orm.PositionModel.open_price,
                orm.PositionModel.open_qty,
            )
            .filter(orm.PositionModel.status == "OPEN")
        )
        if market:
            lot_q = lot_q.filter(orm.PositionModel.market == market)
        lots = lot_q.all()
    finally:
        session.close()

    # 现价按市场分组批量查询（多市场版本看板一次拉全）
    prices: dict[str, float] = {}
    sym_by_mkt: dict[str, set[str]] = {}
    for _, ts_code, mkt, _, _ in lots:
        sym_by_mkt.setdefault(mkt, set()).add(ts_code)
    for mkt, syms in sym_by_mkt.items():
        prices.update(_get_latest_prices(list(syms), mkt))

    counts: dict[str, int] = {}
    costs: dict[str, float] = {}
    values: dict[str, float] = {}
    for sv_id, ts_code, _, open_price, open_qty in lots:
        counts[sv_id] = counts.get(sv_id, 0) + 1
        qty, op = float(open_qty), float(open_price)
        costs[sv_id] = costs.get(sv_id, 0.0) + op * qty
        values[sv_id] = values.get(sv_id, 0.0) + prices.get(ts_code, op) * qty

    prod: list[dict] = []
    staging: list[dict] = []
    draft: list[dict] = []
    for sv_id, version_no, mkt, status, name in rows:
        entry = {
            "strategy_version_id": sv_id,
            "strategy_name": _clean_name(name) or sv_id,
            "market": mkt,
            "version_no": version_no,
            "status": status,
            "position_count": counts.get(sv_id, 0),
            "unrealized_pnl": round(values.get(sv_id, 0.0) - costs.get(sv_id, 0.0), 2),
        }
        if status == "ACTIVE":
            prod.append(entry)
        elif status == "STAGING":
            staging.append(entry)
        else:
            draft.append(entry)

    return {
        "ACTIVE": prod,
        "STAGING": staging,
        "DRAFT": draft,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── 错误趋势 ────────────────────────────────────────────────────────────────

@router.get("/errors/trend")
def errors_trend(market: str = "CN", weeks: int = 8):
    """错误趋势：按周×归因维度计数表（近 N 周）。

    V1 只统计 LOSS 案例挂的负向维度；V2 无 outcome 关联，改用
    pnl_amount < 0 的负向归因记录（对齐 V1「负向归因维度趋势」语义）。
    """
    weeks = max(1, min(weeks, 52))
    now = datetime.now()

    session = get_session()
    try:
        rows = (
            session.query(
                orm.AttributionRecordModel.dimension,
                orm.AttributionRecordModel.created_at,
            )
            .filter(
                orm.AttributionRecordModel.market == market,
                orm.AttributionRecordModel.pnl_amount < 0,
                orm.AttributionRecordModel.created_at >= now - timedelta(weeks=weeks),
            )
            .all()
        )
        all_dims = [
            r[0]
            for r in (
                session.query(orm.AttributionRecordModel.dimension)
                .filter(orm.AttributionRecordModel.market == market)
                .distinct()
                .all()
            )
        ]
    finally:
        session.close()

    dims = [d for d in ATTRIBUTION_DIMS if d in all_dims] + sorted(
        d for d in all_dims if d not in ATTRIBUTION_DIMS
    )

    # 周桶：本周为最后一桶，week_start=周一
    def _week_start(dt: datetime) -> datetime:
        return dt - timedelta(days=dt.weekday())

    this_week = _week_start(now)
    buckets: list[dict] = []
    for w in range(weeks - 1, -1, -1):
        ws = this_week - timedelta(weeks=w)
        buckets.append({"week_start": ws.date().isoformat(), "counts": {d: 0 for d in dims}})

    idx = {b["week_start"]: b for b in buckets}
    for dim, created_at in rows:
        ca = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
        b = idx.get(_week_start(ca).date().isoformat())
        if b is not None:
            b["counts"][dim] = b["counts"].get(dim, 0) + 1

    return {"weeks": buckets, "dimensions": dims}


# ── 非计划动作 ──────────────────────────────────────────────────────────────

@router.get("/non-plan-stats")
def non_plan_stats(market: str = "CN"):
    """非计划动作统计。

    V2 v2_position 无 is_non_plan 列（psql 已确认，V1 对齐导入未含该列），
    返回零值结构占位，字段结构与 V1 get_non_plan_stats 对齐。
    """
    session = get_session()
    try:
        total = int(
            session.query(func.count())
            .select_from(orm.PositionModel)
            .filter(orm.PositionModel.market == market)
            .scalar()
            or 0
        )
    finally:
        session.close()

    return {
        "total_lots": total,
        "non_plan_count": 0,
        "non_plan_rate": 0.0,
        "top_reasons": [],
        "period_days": 30,
    }


# ── 重复错误跟踪 + 行为建议（V1 T312/T313） ─────────────────────────────────

# V1 负向 4 维（BEHAVIOR/ENTRY/RISK/EXIT；SELECTION/MARKET 归环境不计行为错误）
_NEGATIVE_DIMS = ("BEHAVIOR", "ENTRY", "RISK", "EXIT")


def _repeat_errors(market: str = "CN", days: int = 90, min_recurrence: int = 2) -> dict:
    """近 N 天 LOSS 学习案例 → replay → 归因负向维度频次（count≥min_recurrence 才入选）。"""
    from collections import Counter

    session = get_session()
    try:
        since = datetime.now() - timedelta(days=days)
        rows = (
            session.query(orm.AttributionRecordModel.dimension)
            .join(orm.LearningCaseModel,
                  orm.LearningCaseModel.replay_id == orm.AttributionRecordModel.replay_id)
            .filter(orm.LearningCaseModel.outcome == "LOSS",
                    orm.LearningCaseModel.market == market,
                    orm.LearningCaseModel.created_at >= since)
            .all()
        )
        total_loss = len(rows)
    finally:
        session.close()
    counter = Counter(r[0] for r in rows if r[0] in _NEGATIVE_DIMS)
    return {
        "total_loss": total_loss,
        "dimensions": [
            {"dimension": d, "count": c, "rate": round(c / total_loss, 3) if total_loss else 0.0}
            for d, c in counter.most_common() if c >= min_recurrence
        ],
    }


@router.get("/errors/repeat")
def repeat_errors(market: str = "CN", days: int = 90):
    return _repeat_errors(market, days)


# V1 _SUGGESTION_TEMPLATES（每维度首条匹配即用，按优先级排序）
_SUGGESTION_RULES = {
    "BEHAVIOR": [(0.4, "HIGH", "行为纪律频繁失守，建议收紧自动化执行范围并复查人工干预流程"),
                 (0.2, "MEDIUM", "行为偏差有所抬头，建议复盘近期非计划动作并强化执行纪律")],
    "ENTRY":    [(0.4, "HIGH", "入场时机系统性偏差，建议运行 CANDIDATE_PARAM 实验调整入场过滤"),
                 (0.2, "MEDIUM", "入场质量略有下滑，建议关注 ENTRY 决策复盘的追高分位案例")],
    "EXIT":     [(0.4, "HIGH", "出场纪律执行不力，建议运行 RISK_THRESHOLD 实验收紧止损"),
                 (0.2, "MEDIUM", "出场质量一般，建议复查卖在低位的 EXIT 复盘案例")],
    "RISK":     [(0.3, "HIGH", "风控拦截后仍频繁亏损，建议复查风控参数与仓位预算配置"),
                 (0.1, "LOW", "风控维度偶发问题，建议保持观察")],
}


@router.get("/suggestions")
def behavior_suggestions(market: str = "CN", days: int = 90):
    """按重复错误率生成行为改进建议（V1 behavior_suggestion_service 语义）。"""
    data = _repeat_errors(market, days)
    suggestions = []
    for item in data["dimensions"]:
        d, rate = item["dimension"], item["rate"]
        for threshold, priority, text in _SUGGESTION_RULES.get(d, []):
            if rate >= threshold:
                suggestions.append({"dimension": d, "priority": priority,
                                    "rate": rate, "suggestion": text})
                break
    suggestions.sort(key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[s["priority"]])
    return {"market": market, "suggestions": suggestions,
            "no_action_needed": not suggestions}


# ── 红线检查 ────────────────────────────────────────────────────────────────

@router.get("/red-lines")
def red_lines(market: str = "CN", db: PersistencePort = Depends(get_db)):
    """红线清单（现算检查结果）。

    用 AccountStatsService 各策略 position_ratio / today_pnl 对照硬编码红线：
      - 单标的仓位 > 20%（各账户内单一标的市值 / 账户权益）
      - 组合仓位 > 80%
      - 单日亏损 > 初始资金 2%（多账户按合计初始计）
    """
    svc = AccountStatsService(db)
    accounts = svc.by_strategy(market)
    summary = svc.market_summary(market)

    initial = _initial_capital(market)
    lines: list[dict] = []

    # 1. 单标的仓位上限：各账户内单一标的市值 / 账户权益（权益含已实现+浮动，同 by_strategy 口径）
    positions = db.list_open_positions(market=market)
    prices = svc._latest_prices(list({p.ts_code for p in positions}), market)
    realized_map = svc._realized_pnl_by_strategy(market)

    groups: dict[str, list] = {}
    for p in positions:
        groups.setdefault(p.strategy_version_id, []).append(p)

    worst: tuple[str, float, str] | None = None
    for sv_id, lots in groups.items():
        realized = realized_map.get(sv_id, 0.0)
        mv_total = sum(
            prices.get(l.ts_code, float(l.open_price)) * float(l.open_qty) for l in lots
        )
        cost = sum(float(l.open_price) * float(l.open_qty) for l in lots)
        equity = initial + realized + mv_total - cost
        by_sym: dict[str, float] = {}
        for l in lots:
            cur = prices.get(l.ts_code, float(l.open_price))
            by_sym[l.ts_code] = by_sym.get(l.ts_code, 0.0) + cur * float(l.open_qty)
        for sym, mv in by_sym.items():
            ratio = mv / equity * 100 if equity > 0 else 0.0
            if worst is None or ratio > worst[1]:
                name = next(
                    (a["strategy_name"] for a in accounts if a["strategy_version_id"] == sv_id),
                    sv_id,
                )
                worst = (sym, ratio, name)

    if worst is not None:
        lines.append({
            "line_name": "单标的仓位上限",
            "threshold": f"{SINGLE_SECURITY_POSITION_LIMIT:.0f}%",
            "current": f"{worst[1]:.2f}%",
            "status": "BREACH" if worst[1] > SINGLE_SECURITY_POSITION_LIMIT else "PASS",
            "detail": f"最高单标的 {worst[0]}（{worst[2]}）占账户权益 {worst[1]:.2f}%",
        })
    else:
        lines.append({
            "line_name": "单标的仓位上限",
            "threshold": f"{SINGLE_SECURITY_POSITION_LIMIT:.0f}%",
            "current": "—",
            "status": "PASS",
            "detail": "当前无持仓",
        })

    # 2. 组合仓位上限
    ratio = summary["position_ratio"]
    lines.append({
        "line_name": "组合仓位上限",
        "threshold": f"{PORTFOLIO_POSITION_LIMIT:.0f}%",
        "current": f"{ratio:.2f}%",
        "status": "BREACH" if ratio > PORTFOLIO_POSITION_LIMIT else "PASS",
        "detail": (
            f"全市场持仓市值 {summary['market_value']:,.0f} / 权益 "
            f"{summary['total_equity']:,.0f}（{summary['account_count']} 账户合计）"
        ),
    })

    # 3. 单日亏损上限（初始资金 2%）
    today_pnl = summary["today_pnl"]
    total_initial = initial * max(1, summary["account_count"])
    limit_amount = total_initial * DAILY_LOSS_LIMIT_PCT / 100
    lines.append({
        "line_name": "单日亏损上限",
        "threshold": f"-{DAILY_LOSS_LIMIT_PCT:.0f}%（{-limit_amount:,.0f}）",
        "current": f"{today_pnl:,.2f}",
        "status": "BREACH" if today_pnl < -limit_amount else "PASS",
        "detail": (
            f"今日盈亏 {today_pnl:,.2f}，限额 -{limit_amount:,.0f}"
            f"（初始 {total_initial:,.0f} × {DAILY_LOSS_LIMIT_PCT:.0f}%）"
        ),
    })

    return {"lines": lines, "market": market}
