# -*- coding: utf-8 -*-
"""前端 → Hermes Agent 交接队列（只用于人工/智能体协同，不写选股结果）。

- 前端 POST /agent-handoff 创建问题；
- Hermes MCP 用内部 token 拉 pending / claim / answer；
- 前端轮询 GET /agent-handoff/{id} 显示回答。
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/agent-handoff", tags=["agent-handoff"])
INTERNAL_TOKEN = os.environ.get("ZHICE_HANDOFF_TOKEN", "zhice-local-handoff")


def _session():
    return get_session()


def _check_internal(token: str | None):
    if token != INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail={"code": "handoff.internal_token_required"})


@router.post("")
def create_handoff(body: dict, user: User = Depends(get_current_user)):
    q = str(body.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail={"code": "handoff.question_required"})
    ctx = body.get("context") or {}
    page = str(body.get("page") or "")
    s = _session()
    try:
        row = s.execute(text("""
            INSERT INTO v2_agent_handoff (question, context, page, status, created_by)
            VALUES (:q, CAST(:c AS jsonb), :p, 'pending', :u)
            RETURNING id, status, created_at
        """), {"q": q, "c": json.dumps(ctx, ensure_ascii=False), "p": page, "u": user.user_id}).mappings().first()
        s.commit()
        return {"id": row["id"], "status": row["status"]}
    finally:
        s.close()


@router.get("/pending")
def pending(limit: int = 10, x_handoff_token: str | None = Header(default=None)):
    _check_internal(x_handoff_token)
    s = _session()
    try:
        rows = s.execute(text("""
            SELECT id, question, context, page, created_by, created_at
            FROM v2_agent_handoff WHERE status='pending' ORDER BY id LIMIT :n
        """), {"n": max(1, min(limit, 50))}).mappings().all()
        return {"items": [dict(r) for r in rows]}
    finally:
        s.close()


@router.post("/{handoff_id}/claim")
def claim(handoff_id: int, x_handoff_token: str | None = Header(default=None)):
    _check_internal(x_handoff_token)
    s = _session()
    try:
        row = s.execute(text("""
            UPDATE v2_agent_handoff SET status='claimed', claimed_at=now()
            WHERE id=:i AND status='pending' RETURNING id, status
        """), {"i": handoff_id}).mappings().first()
        s.commit()
        if not row:
            raise HTTPException(status_code=409, detail={"code": "handoff.not_pending"})
        return dict(row)
    finally:
        s.close()


@router.post("/{handoff_id}/answer")
def answer(handoff_id: int, body: dict, x_handoff_token: str | None = Header(default=None)):
    _check_internal(x_handoff_token)
    text_answer = str(body.get("answer") or "").strip()
    if not text_answer:
        raise HTTPException(status_code=422, detail={"code": "handoff.answer_required"})
    status = str(body.get("status") or "answered")
    if status not in ("answered", "failed"):
        raise HTTPException(status_code=422, detail={"code": "handoff.bad_status"})
    s = _session()
    try:
        row = s.execute(text("""
            UPDATE v2_agent_handoff
            SET status=:st, answer=:a, answered_at=now()
            WHERE id=:i AND status IN ('pending','claimed') RETURNING id, status
        """), {"st": status, "a": text_answer, "i": handoff_id}).mappings().first()
        s.commit()
        if not row:
            raise HTTPException(status_code=409, detail={"code": "handoff.not_open"})
        return dict(row)
    finally:
        s.close()


@router.get("/my")
def my_handoffs(status: str | None = None, limit: int = 30, user: User = Depends(get_current_user)):
    s = _session()
    try:
        sql = "SELECT id, question, context, page, status, created_by, answer, created_at, claimed_at, answered_at FROM v2_agent_handoff WHERE (created_by=:u OR created_by='autopilot')"
        params = {"u": user.user_id, "n": max(1, min(limit, 100))}
        if status:
            sql += " AND status=:st"
            params["st"] = status
        sql += " ORDER BY id DESC LIMIT :n"
        rows = s.execute(text(sql), params).mappings().all()
        return {"items": [dict(r) for r in rows], "count": len(rows)}
    finally:
        s.close()


@router.get("/{handoff_id}")
def get_handoff(handoff_id: int, user: User = Depends(get_current_user)):
    s = _session()
    try:
        row = s.execute(text("""
            SELECT id, question, context, page, status, created_by, answer, created_at, claimed_at, answered_at
            FROM v2_agent_handoff WHERE id=:i
        """), {"i": handoff_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail={"code": "handoff.not_found"})
        if row["created_by"] != user.user_id and user.role != "ADMIN":
            raise HTTPException(status_code=403, detail={"code": "handoff.forbidden"})
        return dict(row)
    finally:
        s.close()
