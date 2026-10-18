"""决策复盘 API（V1 decision-reviews 对齐）。

数据来自 v2_decision_review（V1 导入），三类决策复盘：
- CANDIDATE 选股质量：决策 5 个交易日后按行业超额回看打分
- ENTRY      建仓时机：当日收盘后按入场价在当日区间分位打分
- MISSED     错过复盘：被过滤/拦截标的的反事实收益评价
（V2 导入数据另含 EXIT 出场时机评审，一并支持）

原始 verdict 是中文叙述（如「买在当日价格区间的82%分位——追高了」），
本模块按 V1 打分规则把结论映射为 GOOD/FAIR/POOR 三档：
- ENTRY:  时机很好→GOOD  时机一般→FAIR  追高了→POOR
- EXIT:   卖在高位→GOOD  时机一般→FAIR  卖在低位→POOR
- MISSED: 过滤正确→GOOD  （无 FAIR）     错过利润→POOR
- CANDIDATE: 无文字档位，按行业超额分 |score|<5pp 为 FAIR，≥+5pp GOOD，≤-5pp POOR
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/replay/decision-reviews", tags=["decision-reviews"], dependencies=[Depends(get_current_user)])
# 错误统计挂在 /replay 下（与决策复盘同域）
replay_router = APIRouter(prefix="/replay", tags=["decision-reviews"], dependencies=[Depends(get_current_user)])

# V1 语义类型名 → 库内 decision_type
CANONICAL_TYPE = {
    "SELECTION_QUALITY": "CANDIDATE",
    "ENTRY_TIMING": "ENTRY",
    "MISSED_REVIEW": "MISSED",
}

# verdict 文字档位 → 三档结论（子串匹配）
_GOOD_TOKENS = ("时机很好", "卖在高位", "是正确的")
_FAIR_TOKENS = ("时机一般",)
_POOR_TOKENS = ("追高了", "卖在低位", "错过了利润")

# CANDIDATE 无文字档位时的分数分档（行业超额百分点）
_CANDIDATE_FAIR_BAND = 5.0


def _grade(decision_type: str, verdict: str | None, score) -> str:
    """把 V1 叙述式 verdict / 分数映射为 GOOD/FAIR/POOR。"""
    v = verdict or ""
    if v:
        if any(t in v for t in _GOOD_TOKENS):
            return "GOOD"
        if any(t in v for t in _FAIR_TOKENS):
            return "FAIR"
        if any(t in v for t in _POOR_TOKENS):
            return "POOR"
    if decision_type == "CANDIDATE" and score is not None:
        s = float(score)
        if s >= _CANDIDATE_FAIR_BAND:
            return "GOOD"
        if s <= -_CANDIDATE_FAIR_BAND:
            return "POOR"
        return "FAIR"
    if decision_type == "MISSED":
        # MISSED 无 FAIR（对齐 V1 语义）：按 score 分档——错过下跌=GOOD，错过上涨=POOR；
        # 缺 score 归 POOR（存疑漏单，待人工核）。此前一律兜底 FAIR 与文档矛盾。
        if score is not None:
            return "GOOD" if float(score) < 0 else "POOR"
        return "POOR"
    return "FAIR"


def _resolve_type(review_type: str | None) -> str | None:
    if not review_type:
        return None
    return CANONICAL_TYPE.get(review_type, review_type)


@router.get("")
def list_reviews(market: str = "CN", review_type: str | None = None, limit: int = 100):
    """决策复盘列表（review_type: SELECTION_QUALITY/ENTRY_TIMING/MISSED_REVIEW，
    也接受库内原始类型 CANDIDATE/ENTRY/EXIT/MISSED）。

    join 策略名与证券名称，按 review_date 倒序（同日按得分倒序）。
    verdict 为原始叙述，grade 为映射后的 GOOD/FAIR/POOR。
    """
    dt = _resolve_type(review_type)
    session = get_session()
    try:
        q = (
            session.query(
                orm.DecisionReviewModel.review_id,
                orm.DecisionReviewModel.review_date,
                orm.DecisionReviewModel.decision_type,
                orm.DecisionReviewModel.ts_code,
                orm.InstrumentModel.name,
                orm.StrategyModel.strategy_name,
                orm.DecisionReviewModel.score,
                orm.DecisionReviewModel.verdict,
                orm.DecisionReviewModel.metrics,
            )
            .join(
                orm.StrategyVersionModel,
                orm.StrategyVersionModel.strategy_version_id == orm.DecisionReviewModel.strategy_version_id,
                isouter=True,
            )
            .join(
                orm.StrategyModel,
                orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id,
                isouter=True,
            )
            .join(
                orm.InstrumentModel,
                (orm.InstrumentModel.symbol == orm.DecisionReviewModel.ts_code)
                & (orm.InstrumentModel.market == orm.DecisionReviewModel.market),
                isouter=True,
            )
            .filter(orm.DecisionReviewModel.market == market)
        )
        if dt:
            q = q.filter(orm.DecisionReviewModel.decision_type == dt)
        rows = (
            q.order_by(
                orm.DecisionReviewModel.review_date.desc(),
                orm.DecisionReviewModel.score.desc(),
                orm.DecisionReviewModel.id.desc(),
            )
            .limit(max(1, min(limit, 500)))
            .all()
        )
        return [
            {
                "review_id": r[0],
                "review_date": r[1].isoformat() if r[1] else None,
                "decision_type": r[2],
                "ts_code": r[3],
                "name": r[4],
                "strategy_name": r[5],
                "score": float(r[6]) if r[6] is not None else None,
                "verdict": r[7],
                "grade": _grade(r[2], r[7], r[6]),
                "metrics": r[8] or {},
            }
            for r in rows
        ]
    finally:
        session.close()


@router.get("/summary")
def reviews_summary(market: str = "CN"):
    """汇总：各类型 {count, avg_score, good/fair/poor} + 当日最佳（score 最高前3）/最差（最低前3）标的。"""
    session = get_session()
    try:
        rows = (
            session.query(
                orm.DecisionReviewModel.decision_type,
                orm.DecisionReviewModel.review_date,
                orm.DecisionReviewModel.ts_code,
                orm.DecisionReviewModel.score,
                orm.DecisionReviewModel.verdict,
            )
            .filter(orm.DecisionReviewModel.market == market)
            .order_by(orm.DecisionReviewModel.review_date.desc())
            .all()
        )
    finally:
        session.close()

    by_type: dict[str, dict] = {}
    for dt, review_date, ts_code, score, verdict in rows:
        bucket = by_type.setdefault(
            dt, {"count": 0, "avg_score": 0.0, "good": 0, "fair": 0, "poor": 0, "_scores": []}
        )
        bucket["count"] += 1
        if score is not None:
            bucket["_scores"].append(float(score))
        bucket[_grade(dt, verdict, score).lower()] += 1

    types: dict[str, dict] = {}
    for dt, b in by_type.items():
        scores = b.pop("_scores")
        b["avg_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        types[dt] = b

    # 当日（最新 review_date）最佳 / 最差：按 score 取前3 / 后3
    latest_date = rows[0][1] if rows else None
    latest_rows = [r for r in rows if r[1] == latest_date] if latest_date else []

    def _pick(records, reverse: bool):
        scored = [r for r in records if r[3] is not None]
        scored.sort(key=lambda r: float(r[3]), reverse=reverse)
        return [
            {
                "review_date": r[1].isoformat() if r[1] else None,
                "decision_type": r[0],
                "ts_code": r[2],
                "score": float(r[3]),
                "verdict": r[4],
                "grade": _grade(r[0], r[4], r[3]),
            }
            for r in scored[:3]
        ]

    return {
        "market": market,
        "review_date": latest_date.isoformat() if latest_date else None,
        "types": types,
        "best": _pick(latest_rows, True),
        "worst": _pick(latest_rows, False),
    }


@replay_router.get("/attribution-stats")
def attribution_stats(market: str = "CN"):
    """错误统计：6 归因维度计数（选股/入场/出场/市场/风控/行为），v2_attribution_record group by dimension。"""
    session = get_session()
    try:
        rows = (
            session.query(
                orm.AttributionRecordModel.dimension,
                func.count(),
                func.coalesce(func.sum(orm.AttributionRecordModel.pnl_amount), 0),
            )
            .filter(orm.AttributionRecordModel.market == market)
            .group_by(orm.AttributionRecordModel.dimension)
            .all()
        )
    finally:
        session.close()

    total = sum(int(r[1]) for r in rows)
    items = [
        {
            "dimension": r[0],
            "count": int(r[1]),
            "pnl_amount": round(float(r[2]), 2),
            "pct": round(int(r[1]) / total, 4) if total else 0.0,
        }
        for r in sorted(rows, key=lambda r: int(r[1]), reverse=True)
    ]
    return {"market": market, "total": total, "dimensions": items}
