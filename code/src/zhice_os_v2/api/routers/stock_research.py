"""ACAD1 逐票投研卡 API：池内股票 → 投研 → 审核 → 交接盯盘。

只写研究记录，不写交易/不改策略。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/stock-research", tags=["stock-research"],
                   dependencies=[Depends(get_current_user)])


class CardBody(BaseModel):
    pool_date: date
    strategy_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)
    name: str = ""
    direction: str = "中性"
    score: float | None = None
    rating: str = "C"
    thesis: str = ""
    catalysts: str = ""
    risks: str = ""
    buy_zone_low: float | None = None
    buy_zone_high: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    invalidation: str = ""
    executable: str = "观察"
    exec_note: str = ""
    data_date: date | None = None


class ReviewBody(BaseModel):
    pool_date: date
    strategy_id: str
    code: str
    verdict: str
    review_note: str = ""


class HandoffBody(BaseModel):
    pool_date: date
    strategy_id: str
    code: str


def _one(sql: str, **params) -> dict[str, Any] | None:
    s = get_session()
    try:
        r = s.execute(text(sql), params).mappings().first()
        return dict(r) if r else None
    finally:
        s.close()


@router.get("")
def list_cards(pool_date: str | None = None, _: User = Depends(get_current_user)):
    params: dict[str, Any] = {}
    where = ""
    if pool_date:
        try:
            day = date.fromisoformat(pool_date)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "research.invalid_date"})
        params["d"] = day
        where = "WHERE p.pool_date = :d"
    else:
        where = "WHERE p.pool_date = (SELECT max(pool_date) FROM v2_acad1_pool)"
    s = get_session()
    try:
        rows = s.execute(text("""
            SELECT p.pool_date, p.market, p.strategy_id, p.code, p.name, p.signal,
                   p.selection_reason, p.data_date,
                   COALESCE(r.direction, '未研究') AS direction,
                   r.score, COALESCE(r.rating, '—') AS rating,
                   r.thesis, r.catalysts, r.risks,
                   r.buy_zone_low, r.buy_zone_high, r.target_price, r.stop_price,
                   r.invalidation, COALESCE(r.executable, '未定') AS executable,
                   r.exec_note, COALESCE(r.status, 'draft') AS status,
                   r.reviewer, r.review_note, r.reviewed_at, r.handoff_at
            FROM v2_acad1_pool p
            LEFT JOIN v2_stock_research r
              ON r.pool_date=p.pool_date AND r.strategy_id=p.strategy_id AND r.code=p.code
            %s
            ORDER BY p.code
        """ % where), params).mappings().all()
        return {"rows": [dict(x) for x in rows]}
    finally:
        s.close()


@router.post("/save")
def save_card(body: CardBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        pool = s.execute(text(
            "SELECT 1 FROM v2_acad1_pool WHERE pool_date=:d AND strategy_id=:s AND code=:c"),
            {"d": body.pool_date, "s": body.strategy_id, "c": body.code}).first()
        if not pool:
            raise HTTPException(status_code=404, detail={"code": "research.not_in_pool"})
        t = pg_insert(sa_table()).values(**body.model_dump())
        t = t.on_conflict_do_update(
            constraint="uq_v2_stock_research_item",
            set_={k: t.excluded[k] for k in (
                "direction", "score", "rating", "thesis", "catalysts", "risks",
                "buy_zone_low", "buy_zone_high", "target_price", "stop_price",
                "invalidation", "executable", "exec_note", "data_date", "name")}
            | {"updated_at": sa.text("now()")},
        )
        s.execute(t)
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@router.post("/review")
def review(body: ReviewBody, _: User = Depends(get_current_user)):
    if body.verdict not in {"通过", "观察", "否决"}:
        raise HTTPException(status_code=400, detail={"code": "research.invalid_verdict"})
    s = get_session()
    try:
        r = s.execute(text("""
            UPDATE v2_stock_research
            SET status = :st, reviewer='ACAD1', review_note=:note, reviewed_at=now(),
                updated_at=now()
            WHERE pool_date=:d AND strategy_id=:s AND code=:c
            RETURNING id
        """), {"st": "reviewed" if body.verdict != "否决" else "rejected",
                 "note": body.review_note, "d": body.pool_date,
                 "s": body.strategy_id, "c": body.code}).first()
        s.commit()
        if not r:
            raise HTTPException(status_code=404, detail={"code": "research.card_missing"})
        return {"ok": True, "status": "reviewed" if body.verdict != "否决" else "rejected"}
    finally:
        s.close()


@router.post("/handoff")
def handoff(body: HandoffBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        r = s.execute(text("""
            UPDATE v2_stock_research
            SET status='handoff', handoff_at=now(), updated_at=now()
            WHERE pool_date=:d AND strategy_id=:s AND code=:c AND status='reviewed'
            RETURNING id
        """), {"d": body.pool_date, "s": body.strategy_id, "c": body.code}).first()
        s.commit()
        if not r:
            raise HTTPException(status_code=409, detail={"code": "research.not_reviewed"})
        return {"ok": True, "status": "handoff"}
    finally:
        s.close()


def sa_table():
    return sa.table("v2_stock_research", *[sa.column(c) for c in (
        "pool_date", "strategy_id", "code", "direction", "score", "rating",
        "thesis", "catalysts", "risks", "buy_zone_low", "buy_zone_high",
        "target_price", "stop_price", "invalidation", "executable", "exec_note",
        "data_date", "name", "updated_at")])
