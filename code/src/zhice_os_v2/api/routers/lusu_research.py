"""鲁肃军团判断 API：只读 ACAD1 定稿池 + 写判断记录（研究判断，不下单）。"""
from __future__ import annotations

from datetime import date
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/lusu-research", tags=["lusu-research"],
                   dependencies=[Depends(get_current_user)])

_VERDICTS = {"通过", "观察", "否决"}


class DecisionBody(BaseModel):
    pool_date: date
    strategy_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)
    name: str = ""
    verdict: str
    reasons: str = ""
    risk_checks: list[str] = Field(default_factory=list)
    position_hint: str = ""


def _rows(sql: str, **params) -> list[dict[str, Any]]:
    session = get_session()
    try:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]
    finally:
        session.close()


@router.get("/queue")
def queue(_: User = Depends(get_current_user)):
    rows = _rows("""
        SELECT p.pool_date, p.market, p.strategy_id, p.code, p.name, p.signal, p.score,
               p.selection_reason, p.data_date,
               d.verdict, d.reasons, d.risk_checks, d.position_hint,
               d.decided_by, d.decided_at
        FROM v2_acad1_pool p
        LEFT JOIN v2_lusu_decision d
          ON d.pool_date = p.pool_date AND d.strategy_id = p.strategy_id AND d.code = p.code
        WHERE p.pool_date = (SELECT max(pool_date) FROM v2_acad1_pool)
        ORDER BY p.signal, p.code
    """)
    return {"pool_date": rows[0]["pool_date"] if rows else None, "rows": rows}


@router.post("/decision")
def decide(body: DecisionBody, _: User = Depends(get_current_user)):
    if body.verdict not in _VERDICTS:
        raise HTTPException(status_code=400, detail={"code": "lusu.invalid_verdict"})
    session = get_session()
    try:
        exists = session.execute(text(
            "SELECT 1 FROM v2_acad1_pool WHERE pool_date=:d AND strategy_id=:s "
            "AND code=:c LIMIT 1"), {"d": body.pool_date, "s": body.strategy_id,
                                       "c": body.code}).first()
        if not exists:
            raise HTTPException(status_code=404, detail={"code": "lusu.pool_item_not_found"})
        t = text("""
            INSERT INTO v2_lusu_decision
                (pool_date, strategy_id, code, name, verdict, reasons, risk_checks,
                 position_hint, decided_by)
            VALUES
                (:pool_date, :strategy_id, :code, :name, :verdict, :reasons,
                 CAST(:risk_checks AS jsonb), :position_hint, '鲁肃(D1)')
            ON CONFLICT ON CONSTRAINT uq_v2_lusu_decision_item DO UPDATE SET
                verdict = EXCLUDED.verdict,
                reasons = EXCLUDED.reasons,
                risk_checks = EXCLUDED.risk_checks,
                position_hint = EXCLUDED.position_hint,
                decided_at = now()
            RETURNING pool_date, strategy_id, code, verdict, reasons, decided_by, decided_at
        """)
        row = session.execute(t, {
            "pool_date": body.pool_date,
            "strategy_id": body.strategy_id,
            "code": body.code,
            "name": body.name,
            "verdict": body.verdict,
            "reasons": body.reasons,
            "risk_checks": json.dumps(body.risk_checks, ensure_ascii=False),
            "position_hint": body.position_hint,
        }).mappings().first()
        session.commit()
        return dict(row) if row else {}
    finally:
        session.close()
