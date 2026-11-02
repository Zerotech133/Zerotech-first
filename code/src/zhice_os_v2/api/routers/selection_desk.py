"""每日选股中台 API：日报 → 选股 → 统一池 → 下游反馈。

P2-2：日报按市场独立（v2_selection_report 唯一键 = report_date+strategy_id+market）。

- GET /selection-desk?market=CN 只返回该市场的日报/统一池/溯源/反馈，不再把
  market='MULTI' 的跨市场日报塞进所有市场；
- POST /selection-desk/report 允许一次提交跨市场选股：按 pick 的市场（显式
  market 字段，缺省按代码前缀推断）拆成多条按市场日报写入，从源头消灭 MULTI。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
import sqlalchemy as sa

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/selection-desk", tags=["selection-desk"],
                   dependencies=[Depends(get_current_user)])

MARKETS: tuple[str, ...] = ("CN", "HK", "US", "CRYPTO")
_STRATEGY_MARKETS = (
    ("v1-us", "US"),
    ("v1-hk", "HK"),
    ("v1-crypto", "CRYPTO"),
    ("v1-cn", "CN"),
)


def _market_from_code(code: str) -> str | None:
    """按代码前缀/后缀推断市场；无法判断返回 None。"""
    c = (code or "").strip().lower()
    if not c:
        return None
    if c.startswith("rt_hk") or c.startswith("hk") or c.endswith((".hk", ".hkg")):
        return "HK"
    if c.startswith("gb_") or c.endswith((".us", ".nasdaq", ".nyse")):
        return "US"
    if c.startswith(("sh", "sz", "bj")) or c.endswith((".sh", ".sz", ".bj")):
        return "CN"
    if "/" in c and c.rsplit("/", 1)[-1] in ("usdt", "usdc", "btc", "eth"):
        return "CRYPTO"
    return None


def _market_from_strategy(strategy_id: str) -> str | None:
    s = (strategy_id or "").strip().lower()
    for prefix, market in _STRATEGY_MARKETS:
        if s.startswith(prefix):
            return market
    return None


def normalize_market(value: str | None, code: str = "", strategy_id: str = "") -> str:
    """market 归一为 CN/HK/US/CRYPTO；空值/MULTI/未知值按 code、策略前缀回退。"""
    m = (value or "").strip().upper()
    if m in MARKETS:
        return m
    return (_market_from_code(code) or _market_from_strategy(strategy_id)
            or _market_from_code(strategy_id) or "CN")


def _parse_market(market: str | None) -> str | None:
    if not market:
        return None
    m = market.strip().upper()
    if m not in MARKETS:
        raise HTTPException(status_code=400, detail={
            "code": "desk.invalid_market", "allowed": list(MARKETS)})
    return m


class PickBody(BaseModel):
    code: str
    name: str = ""
    signal_type: str = ""
    score: float | None = None
    reason: str = ""
    # 可选显式市场；缺省按 code 推断（跨市场提交由后端拆分）。
    market: str = ""


class ReportBody(BaseModel):
    report_date: date
    strategy_id: str
    analyst: str = ""
    market_view: str = ""
    yesterday_review: str = ""
    lessons: str = ""
    # 无 picks 的日报（研究态策略）用它声明市场；缺省按策略前缀推断。
    market: str = ""
    picks: list[PickBody] = Field(default_factory=list)


class SupervisionBody(BaseModel):
    supervision_date: date
    strategy_id: str
    note: str = ""
    score: float | None = None


class FeedbackBody(BaseModel):
    pool_date: date
    code: str
    consumer: str = "LUSU"
    decision: str
    reason: str = ""
    result: str = ""


def _group_picks_by_market(strategy_id: str, picks: list[PickBody]) -> dict[str, list[PickBody]]:
    """按 pick 市场分组；同一策略一笔提交可跨市场，落库时拆成多条日报。"""
    groups: dict[str, list[PickBody]] = {}
    for pick in picks:
        market = normalize_market(pick.market, pick.code, strategy_id)
        groups.setdefault(market, []).append(pick)
    return groups


def _aggregate(s, day: date) -> int:
    s.execute(text("DELETE FROM v2_selection_pool WHERE pool_date=:d"), {"d": day})
    rows = s.execute(text("""
        SELECT code, max(name) AS name, max(market) AS market,
               string_agg(DISTINCT strategy_id, ',') AS src,
               string_agg(DISTINCT signal_type, ',') AS sigs,
               max(score) AS mx
        FROM v2_selection_pick WHERE report_date=:d GROUP BY code
    """), {"d": day}).mappings().all()
    if rows:
        t = sa.table("v2_selection_pool", *[sa.column(x) for x in (
            "pool_date", "market", "code", "name", "source_strategies",
            "signal_types", "max_score", "status")])
        s.execute(pg_insert(t).values([dict(
            pool_date=day, market=r["market"] or "CN", code=r["code"],
            name=r["name"] or "", source_strategies=r["src"] or "",
            signal_types=r["sigs"] or "", max_score=r["mx"], status="published"
        ) for r in rows]))
    return len(rows)


@router.get("")
def desk(date_str: str | None = None, market: str | None = None,
         _: User = Depends(get_current_user)):
    params: dict[str, Any] = {}
    conds: list[str] = []
    target_market = _parse_market(market)
    if target_market:
        params["m"] = target_market
        conds.append("r.market=:m")
    if date_str:
        try:
            params["d"] = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "desk.invalid_date"})
        conds.append("r.report_date=:d")
    else:
        # 未指定日期：每个 (策略, 市场) 取自己最新的日报（不同市场数据日可不同）。
        conds.append(
            "r.report_date=(SELECT max(r2.report_date) FROM v2_selection_report r2 "
            "WHERE r2.strategy_id=r.strategy_id AND r2.market=r.market)")
    where = "WHERE " + " AND ".join(conds) if conds else ""
    s = get_session()
    try:
        reports = s.execute(text("""
            SELECT r.id, r.report_date, r.strategy_id, r.analyst, r.market_view, r.market,
                   r.yesterday_review, r.lessons, r.status,
                   (SELECT count(*) FROM v2_selection_pick p WHERE p.report_id=r.id) AS picks
            FROM v2_selection_report r %s ORDER BY r.market, r.strategy_id
        """ % where), params).mappings().all()
        day = max((r["report_date"] for r in reports), default=None)
        # 池子口径日可能晚于本市场日报日（平台适配器按全局 max(report_date) 写
        # pool_date）。按市场自动取最新时，各市场用自己最新的池日，避免美股 tab
        # 因数据日滞后一天而显示空池；显式 date_str 查询仍严格按该日期。
        pool_day = day
        if target_market and day and not date_str:
            pool_day = s.execute(text(
                "SELECT max(pool_date) FROM v2_selection_pool WHERE market=:m"),
                {"m": target_market}).scalar() or day
        pool, feedback = [], []
        pool_filter = " AND market=:m" if target_market else ""
        if pool_day:
            pool = s.execute(text(
                "SELECT pool_date, market, code, name, source_strategies, signal_types, "
                "max_score FROM v2_selection_pool WHERE pool_date=:d" + pool_filter +
                " ORDER BY market, code"),
                {"d": pool_day, **({"m": target_market} if target_market else {})}
            ).mappings().all()
            feedback_params = {"d": pool_day}
            feedback_filter = ""
            if target_market:
                feedback_params["m"] = target_market
                feedback_filter = (
                    " AND f.code IN (SELECT code FROM v2_selection_pool "
                    "WHERE pool_date=:d AND market=:m)")
            feedback = s.execute(text(
                "SELECT f.pool_date, f.code, f.consumer, f.decision, f.reason, f.result, "
                "f.created_at FROM v2_selection_feedback f WHERE f.pool_date=:d" +
                feedback_filter + " ORDER BY f.created_at DESC"),
                feedback_params).mappings().all()
        detail_params: dict[str, Any] = {"d": pool_day}
        detail_filter = ""
        if target_market:
            detail_params["m"] = target_market
            detail_filter = " AND p.market=:m"
        detail_sql = text("""
            SELECT p.market, p.code, p.name, p.source_strategies, p.signal_types, p.max_score,
                   json_agg(json_build_object(
                     'strategy_id', rp.strategy_id,
                     'report_date', rp.report_date,
                     'signal_type', rp.signal_type,
                     'score', rp.score,
                     'reason', rp.reason
                   ) ORDER BY rp.strategy_id) AS sources
            FROM v2_selection_pool p
            JOIN v2_selection_pick rp
              ON rp.code=p.code
             AND (rp.strategy_id, rp.market, rp.report_date) IN (
               SELECT strategy_id, market, max(report_date)
               FROM v2_selection_pick GROUP BY strategy_id, market)
            WHERE p.pool_date=:d""" + detail_filter + """
            GROUP BY p.market, p.code,p.name,p.source_strategies,p.signal_types,p.max_score
            ORDER BY p.market, p.code
        """)
        pool_detail = (s.execute(detail_sql, detail_params).mappings().all()
                       if pool_day else [])
        coverage_params: dict[str, Any] = {}
        coverage_filter = ""
        if target_market:
            coverage_params["m"] = target_market
            coverage_filter = " AND r.market=:m"
        coverage_sql = text("""
            WITH reg AS (
              SELECT strategy_id, display_name AS name, 'sidecar' AS source FROM v2_strategy_sidecar
              UNION
              SELECT strategy_id, strategy_name, 'platform' FROM v2_strategy
              WHERE visibility='PUBLIC' AND publication_status='PUBLISHED'
            )
            SELECT reg.strategy_id, reg.name, reg.source,
                   max(r.report_date) AS report_date,
                   coalesce(sum((SELECT count(*) FROM v2_selection_pick p WHERE p.report_id=r.id)),0) AS picks
            FROM reg
            LEFT JOIN v2_selection_report r ON r.strategy_id=reg.strategy_id""" +
            coverage_filter + """
            GROUP BY reg.strategy_id, reg.name, reg.source
            ORDER BY reg.strategy_id
        """)
        coverage = s.execute(coverage_sql, coverage_params).mappings().all()
        # 督导是策略级记录（无 market 列），各市场 tab 均可见。
        supervision = s.execute(text(
            "SELECT supervision_date, strategy_id, note, score, supervisor "
            "FROM v2_strategy_supervision WHERE supervision_date=:d ORDER BY strategy_id"),
            {"d": day}).mappings().all() if day else []
        return {"date": day, "pool_date": pool_day, "market": target_market,
                "markets": list(MARKETS),
                "reports": [dict(x) for x in reports],
                "pool": [dict(x) for x in pool],
                "feedback": [dict(x) for x in feedback],
                "supervision": [dict(x) for x in supervision],
                "pool_detail": [dict(x) for x in pool_detail],
                "coverage": [dict(x) for x in coverage]}
    finally:
        s.close()


@router.post("/report")
def submit_report(body: ReportBody, _: User = Depends(get_current_user)):
    """一次提交可跨市场：按市场拆成多条日报（同市场 upsert），从源头消灭 MULTI。"""
    groups = _group_picks_by_market(body.strategy_id, body.picks)
    if not groups:
        fallback = normalize_market(body.market, "", body.strategy_id)
        groups[fallback] = []
    s = get_session()
    try:
        # 同一 (report_date, strategy_id) 下本次未提交的市场日报视为过期，清理。
        stale = s.execute(text(
            "SELECT id, market FROM v2_selection_report "
            "WHERE report_date=:d AND strategy_id=:s"), {"d": body.report_date, "s": body.strategy_id}
        ).mappings().all()
        for row in stale:
            if normalize_market(row["market"], strategy_id=body.strategy_id) in groups:
                continue
            s.execute(text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": row["id"]})
            s.execute(text("DELETE FROM v2_selection_report WHERE id=:r"), {"r": row["id"]})
        report_ids: dict[str, int] = {}
        for market in sorted(groups):
            rid = s.execute(text("""
                INSERT INTO v2_selection_report
                  (report_date, strategy_id, market, analyst, market_view,
                   yesterday_review, lessons, status, submitted_at)
                VALUES (:d, :s, :m, :a, :mv, :yr, :ls, 'submitted', now())
                ON CONFLICT ON CONSTRAINT uq_v2_selection_report DO UPDATE SET
                  analyst=EXCLUDED.analyst, market_view=EXCLUDED.market_view,
                  yesterday_review=EXCLUDED.yesterday_review, lessons=EXCLUDED.lessons,
                  status='submitted', submitted_at=now()
                RETURNING id
            """), {"d": body.report_date, "s": body.strategy_id, "m": market,
                     "a": body.analyst, "mv": body.market_view,
                     "yr": body.yesterday_review, "ls": body.lessons}).scalar()
            report_ids[market] = rid
            s.execute(text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": rid})
            picks = groups[market]
            if picks:
                t = sa.table("v2_selection_pick", *[sa.column(x) for x in (
                    "report_id", "report_date", "strategy_id", "code", "name",
                    "signal_type", "score", "reason", "market")])
                s.execute(pg_insert(t).values([dict(
                    report_id=rid, report_date=body.report_date, strategy_id=body.strategy_id,
                    code=p.code, name=p.name, signal_type=p.signal_type,
                    score=p.score, reason=p.reason, market=market) for p in picks]))
        n = _aggregate(s, body.report_date)
        s.commit()
        ordered = sorted(report_ids)
        return {"ok": True,
                "markets": ordered,
                "report_ids": report_ids,
                # 单市场提交保持旧字段兼容；跨市场提交 report_id 为 None。
                "report_id": report_ids[ordered[0]] if len(ordered) == 1 else None,
                "pool_size": n}
    finally:
        s.close()


@router.post("/feedback")
def feedback(body: FeedbackBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        s.execute(text("""
            INSERT INTO v2_selection_feedback
              (pool_date, code, consumer, decision, reason, result, created_at)
            VALUES (:d, :c, :u, :dec, :r, :res, now())
        """), {"d": body.pool_date, "c": body.code, "u": body.consumer,
                 "dec": body.decision, "r": body.reason, "res": body.result})
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@router.post("/supervision")
def supervision(body: SupervisionBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        s.execute(text("""
            INSERT INTO v2_strategy_supervision
              (supervision_date, strategy_id, note, score, supervisor)
            VALUES (:d, :s, :n, :sc, 'ACAD1')
            ON CONFLICT ON CONSTRAINT uq_v2_strategy_supervision DO UPDATE SET
              note=EXCLUDED.note, score=EXCLUDED.score, created_at=now()
        """), {"d": body.supervision_date, "s": body.strategy_id,
                 "n": body.note, "sc": body.score})
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@router.get("/public-summary")
def public_summary():
    """Hermes 只读 BFF 用：仅返回聚合计数，不含股票明细/理由。"""
    s = get_session()
    try:
        row = s.execute(text("""
            SELECT
              (SELECT max(report_date) FROM v2_selection_report) AS data_date,
              (SELECT count(*) FROM v2_selection_pool) AS pool_total,
              (SELECT count(*) FROM v2_selection_report) AS report_total,
              (SELECT count(*) FROM v2_strategy_supervision) AS supervision_total,
              (SELECT count(*) FROM v2_selection_feedback WHERE decision='用') AS used,
              (SELECT count(*) FROM v2_selection_feedback WHERE decision='不用') AS rejected,
              (SELECT count(*) FROM (
                 SELECT code, count(*) AS n FROM v2_selection_pick p
                 WHERE (p.strategy_id,p.market,p.report_date) IN (
                   SELECT strategy_id,market,max(report_date) FROM v2_selection_pick
                   GROUP BY strategy_id,market)
                 GROUP BY code HAVING count(*)>=2) x) AS resonance
        """)).mappings().first()
        return {"ok": True, "data": dict(row) if row else {}}
    finally:
        s.close()
