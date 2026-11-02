"""ACAD1 研究台只读 API：选池 + 评审 + 证据包（不写平台交易/策略目录/研究卡）。

证据包端点只读 ``C:\\Temp\\acad1_evidence_bundle.json``（ACAD1 证据包生成脚本的输出），
默认路径可用环境变量 ``ACAD1_EVIDENCE_BUNDLE_PATH`` 覆盖（测试/运维用）；不写 DB、不改卡。
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/acad1-research", tags=["acad1-research"],
                   dependencies=[Depends(get_current_user)])

DEFAULT_EVIDENCE_BUNDLE_PATH = r"C:\Temp\acad1_evidence_bundle.json"


def _many(sql: str, **params) -> list[dict[str, Any]]:
    session = get_session()
    try:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]
    finally:
        session.close()


@router.get("/pool")
def pool(
    date_str: str | None = Query(default=None, alias="date"),
    limit: int = Query(default=200, ge=1, le=500),
    _: User = Depends(get_current_user),
):
    params: dict[str, Any] = {"limit": limit}
    if date_str:
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "acad1.invalid_date"})
        params["day"] = day
        rows = _many(
            "SELECT pool_date, strategy_id, code, name, market, signal, score, "
            "selection_reason, selected_by, status, data_date FROM v2_acad1_pool "
            "WHERE pool_date = :day ORDER BY signal, code LIMIT :limit",
            **params,
        )
    else:
        rows = _many(
            "SELECT pool_date, strategy_id, code, name, market, signal, score, "
            "selection_reason, selected_by, status, data_date FROM v2_acad1_pool "
            "WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool) "
            "ORDER BY signal, code LIMIT :limit",
            **params,
        )
    return {"pool_date": rows[0]["pool_date"] if rows else None, "rows": rows}


@router.get("/reviews")
def reviews(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(get_current_user),
):
    rows = _many(
        "SELECT review_date, topic, strategy_id, verdict, summary, evidence, reviewer "
        "FROM v2_acad1_review ORDER BY review_date DESC, id DESC LIMIT :limit",
        limit=limit,
    )
    return {"rows": rows}



@router.get("/stats")
def stats(_: User = Depends(get_current_user)):
    session = get_session()
    try:
        row = session.execute(text("""
            SELECT
              (SELECT count(*) FROM v2_acad1_pool
                 WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool)) AS pool_total,
              (SELECT count(*) FROM v2_strategy_observation o
                 WHERE o.signal_date = (SELECT max(pool_date) FROM v2_acad1_pool)
                   AND o.strategy_id IN (SELECT DISTINCT strategy_id FROM v2_acad1_pool
                       WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool))) AS obs_total,
              (SELECT count(*) FROM v2_strategy_observation o
                 WHERE o.signal_date = (SELECT max(pool_date) FROM v2_acad1_pool)
                   AND o.strategy_id IN (SELECT DISTINCT strategy_id FROM v2_acad1_pool
                       WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool))
                   AND o.status = 'done') AS obs_done,
              (SELECT count(*) FROM v2_strategy_backtest b
                 WHERE b.strategy_id IN (SELECT DISTINCT strategy_id FROM v2_acad1_pool
                       WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool))) AS bt_total,
              (SELECT count(*) FROM v2_acad1_review) AS review_total,
              (SELECT max(pool_date) FROM v2_acad1_pool) AS pool_date
        """)).mappings().first()
        return dict(row) if row else {}
    finally:
        session.close()



@router.get("/decisions")
def decisions(limit: int = 100, _: User = Depends(get_current_user)):
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT d.pool_date, d.strategy_id, d.code, d.name, d.verdict,
                   d.reasons, d.risk_checks, d.position_hint, d.decided_by, d.decided_at
            FROM v2_lusu_decision d
            ORDER BY d.pool_date DESC, d.decided_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return {"rows": [dict(r) for r in rows]}
    finally:
        session.close()


@router.get("/evidence")
def evidence(_: User = Depends(get_current_user)):
    """ACAD1 证据包（只读）：读取 C:\\Temp\\acad1_evidence_bundle.json，返回 summary + items。

    - 不写 DB、不改研究卡、不生成结论；文件不存在 -> 404 并说明“证据包未生成”；
    - 文件存在但损坏/结构不合法 -> 503，避免把半成品当证据返回。
    """
    path = Path(os.environ.get("ACAD1_EVIDENCE_BUNDLE_PATH", DEFAULT_EVIDENCE_BUNDLE_PATH))
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "acad1.evidence_bundle_not_generated",
                "message": "ACAD1 证据包未生成",
                "path": str(path),
                "hint": "请先运行 ACAD1 证据包生成脚本，产出 C:\\Temp\\acad1_evidence_bundle.json 后重试",
            },
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "acad1.evidence_bundle_unreadable",
                "message": "证据包存在但读取/解析失败",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(items, list):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "acad1.evidence_bundle_invalid",
                "message": "证据包结构不合法（顶层需为 object 且 items 为数组）",
                "path": str(path),
            },
        )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    pool_date = payload.get("pool")
    if not isinstance(pool_date, str):
        pool = summary.get("pool")
        pool_date = pool.get("pool_date") if isinstance(pool, dict) else None
    return {
        "schema": payload.get("schema"),
        "generated_at": payload.get("generated_at"),
        "pool_date": pool_date,
        "read_only": bool(payload.get("read_only", True)),
        "source": {"path": str(path), "bytes": path.stat().st_size, "read_only": True},
        "item_count": len(items),
        "summary": summary,
        "items": items,
    }


@router.get("/calendar")
def calendar(limit: int = 60, _: User = Depends(get_current_user)):
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT pool_date, count(*) AS total,
                   string_agg(DISTINCT strategy_id, ',') AS strategies
            FROM v2_acad1_pool
            GROUP BY pool_date
            ORDER BY pool_date DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return {"rows": [dict(r) for r in rows]}
    finally:
        session.close()
