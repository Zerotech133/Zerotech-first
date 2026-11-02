# -*- coding: utf-8 -*-
"""Hermes 只读 BFF：策略/pool/评审/任务状态，供 Hermes MCP/Agent 查询。

只读；不写 DB、不触发任务、不自动通过/否决。
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
import yaml
from fastapi import APIRouter, HTTPException

from zhice_os_v2.config.settings import get_settings

router = APIRouter(tags=["hermes-bff"])
MANIFEST = Path(__file__).resolve().parents[4] / "config" / "strategy_manifests.yaml"
JOB_STATUS = Path(r"C:\Temp\zhice_job_status.json")
EVIDENCE = Path(r"C:\Temp\acad1_evidence_bundle.json")


def _db():
    return sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))


def _clean(row):
    if not isinstance(row, dict):
        if hasattr(row, "isoformat"):
            return row.isoformat()
        if hasattr(row, "as_tuple"):
            return float(row)
        return row
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "as_tuple"):
            out[k] = float(v)
        else:
            out[k] = v
    return out


@router.get("/hermes/capabilities")
def capabilities():
    return {"read_only": True, "agent": "Hermes", "tools": [
        {"name": "zhice_strategy_list", "label": "策略清单", "description": "12 条策略 owner/生命周期/日报"},
        {"name": "zhice_strategy_detail", "label": "策略详情", "description": "单策略 manifest/ownership/版本"},
        {"name": "zhice_pool_today", "label": "ACAD1 池", "description": "最新 30 只池内股票"},
        {"name": "zhice_review_queue", "label": "复核队列", "description": "待复核 draft 研究卡"},
        {"name": "zhice_job_status", "label": "任务状态", "description": "每日任务步骤/数据日/rc"},
        {"name": "zhice_handoff_pending", "label": "待办交接", "description": "前端提交给 Hermes 的问题"},
        {"name": "zhice_handoff_claim", "label": "认领交接", "description": "Hermes 认领一个问题"},
        {"name": "zhice_handoff_answer", "label": "回答交接", "description": "Hermes 回答并回写"},
    ]}


@router.get("/hermes/overview")
def overview():
    """智能体侧与指挥中心共用的数据库对账口径。"""
    e = _db()
    with e.connect() as c:
        def one(sql: str):
            return c.execute(sa.text(sql)).scalar()
        data_date = one("SELECT max(pool_date) FROM v2_acad1_pool")
        fb_date = one("SELECT max(pool_date) FROM v2_selection_feedback")
        cand_date = one("SELECT max(pool_date) FROM v2_candidate_pool")
        cand_rows = c.execute(sa.text("""
            SELECT market, sum(candidate_count) AS n FROM v2_candidate_pool
            WHERE pool_date=(SELECT max(pool_date) FROM v2_candidate_pool) GROUP BY market
        """)).mappings().all()
        cand_asset_rows = c.execute(sa.text("""
            SELECT p.market, count(DISTINCT a.ts_code) AS n
            FROM v2_candidate_asset a JOIN v2_candidate_pool p ON p.pool_id=a.pool_id
            WHERE p.pool_date=(SELECT max(pool_date) FROM v2_candidate_pool)
            GROUP BY p.market
        """)).mappings().all()
        latest_pick_rows = c.execute(sa.text("""
            SELECT r.market, count(pk.id) AS n, max(r.report_date) AS d
            FROM v2_selection_report r
            JOIN v2_selection_pick pk ON pk.report_id=r.id
            WHERE r.report_date=(SELECT max(r2.report_date) FROM v2_selection_report r2 WHERE r2.market=r.market)
            GROUP BY r.market
        """)).mappings().all()
        published_rows = c.execute(sa.text("""
            SELECT market, count(*) AS n FROM v2_selection_pool
            WHERE pool_date=(SELECT max(pool_date) FROM v2_selection_pool) GROUP BY market
        """)).mappings().all()
        sup_date = one("SELECT max(supervision_date) FROM v2_strategy_supervision")
        payload = {
            "read_only": True,
            "data_date": str(data_date) if data_date else None,
            "strategies": one("SELECT count(*) FROM v2_strategy") + one("SELECT count(*) FROM v2_strategy_sidecar"),
            "pool": one("SELECT count(*) FROM v2_acad1_pool WHERE pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)"),
            "review_draft": one("SELECT count(*) FROM v2_stock_research WHERE status='draft'"),
            "candidate_pool_date": str(cand_date) if cand_date else None,
            "candidates": {r["market"]: int(r["n"] or 0) for r in cand_rows},
            "candidate_assets": {r["market"]: int(r["n"] or 0) for r in cand_asset_rows},
            "latest_picks": {r["market"]: int(r["n"] or 0) for r in latest_pick_rows},
            "latest_pick_date": {r["market"]: str(r["d"]) for r in latest_pick_rows},
            "published_pool": {r["market"]: int(r["n"] or 0) for r in published_rows},
            "acad1_reviews": one("SELECT count(*) FROM v2_acad1_review"),
            "feedback": one("SELECT count(*) FROM v2_selection_feedback WHERE pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)"),
            "feedback_total": one("SELECT count(*) FROM v2_selection_feedback"),
            "feedback_date": str(fb_date) if fb_date else None,
            "supervision": one("SELECT count(*) FROM v2_strategy_supervision WHERE supervision_date=(SELECT max(pool_date) FROM v2_acad1_pool)"),
            "supervision_total": one("SELECT count(*) FROM v2_strategy_supervision"),
            "supervision_date": str(sup_date) if sup_date else None,
            "handoffs": {
                "total": one("SELECT count(*) FROM v2_agent_handoff"),
                "pending": one("SELECT count(*) FROM v2_agent_handoff WHERE status IN ('pending','claimed')"),
                "archived": one("SELECT count(*) FROM v2_agent_handoff WHERE status IN ('answered','failed')"),
            },
            "sources": {
                "strategies": "v2_strategy + v2_strategy_sidecar",
                "pool": "v2_acad1_pool latest",
                "review": "v2_stock_research draft",
                "feedback": "v2_selection_feedback",
                "supervision": "v2_strategy_supervision",
                "handoffs": "v2_agent_handoff",
            },
        }
    return payload


@router.get("/hermes/autopilot-status")
def autopilot_status():
    path = Path(r"C:\Temp\agent_autopilot.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "hermes.autopilot_missing"})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "hermes.autopilot_unreadable", "error": str(exc)[:120]})


@router.get("/hermes/acad1-comparison")
def acad1_comparison(market: str = "CN", limit: int = 60):
    """基础 BUY 候选的横向比较：增强排序 vs ACAD1 实际入池。只读。"""
    m = market.upper()
    ev = {}
    try:
        if EVIDENCE.exists():
            for it in json.loads(EVIDENCE.read_text(encoding="utf-8")).get("items") or []:
                key = (it.get("market"), it.get("code"))
                ev[key] = it.get("evidence_level") or "unknown"
                if it.get("pool_code"):
                    ev[(it.get("market"), it.get("pool_code"))] = it.get("evidence_level") or "unknown"
    except Exception:  # noqa: BLE001
        ev = {}
    e = _db()
    with e.connect() as c:
        day = c.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool WHERE market=:m"), {"m": m}).scalar()
        rows = c.execute(sa.text("""
            SELECT sp.code, sp.name, sp.source_strategies, sp.signal_types, sp.max_score, sp.pool_date
            FROM v2_selection_pool sp WHERE sp.market=:m AND sp.pool_date=:d
        """), {"m": m, "d": day}).mappings().all()
        base = {}
        for r in c.execute(sa.text("""
            SELECT DISTINCT ON (code) code, base_state, trigger_rule FROM v2_base_signal
            WHERE market=:m ORDER BY code, trade_date DESC
        """), {"m": m}).mappings().all():
            base[r["code"]] = dict(r)
        acad = {r[0] for r in c.execute(sa.text("""
            SELECT code FROM v2_acad1_pool WHERE market=:m AND pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)
        """), {"m": m}).all()}
    lvl = {"full": 3, "partial": 2, "none": 1}
    out = []
    for r in rows:
        b = base.get(r["code"]) or {}
        if b.get("base_state") != "BUY":
            continue
        srcs = [x for x in str(r["source_strategies"] or "").split(",") if x.strip()]
        evl = ev.get((m, r["code"]), "unknown")
        out.append({
            "code": r["code"], "name": r["name"], "strategy": r["source_strategies"],
            "signal": r["signal_types"], "score": _clean(r["max_score"]),
            "base_state": b.get("base_state"), "trigger_rule": b.get("trigger_rule"),
            "resonance": len(srcs), "evidence": evl,
            "selected": r["code"] in acad,
            "_rank": (len(srcs), lvl.get(evl, 0), float(r["max_score"] or 0)),
        })
    out.sort(key=lambda x: (-x["_rank"][0], -x["_rank"][1], -x["_rank"][2], x["code"]))
    for i, x in enumerate(out, 1):
        x["rank"] = i
        x["reason"] = (
            f"基础买点；共振{x['resonance']}维；证据{x['evidence']}；评分{x['score']}；"
            + ("ACAD1 已入池" if x["selected"] else f"增强排名第{i}，未入当前配额")
        )
        x.pop("_rank", None)
    quota = {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}.get(m, 0)
    expected = min(quota, len(out))
    selected = sum(1 for x in out if x["selected"])
    return {"read_only": True, "market": m, "pool_date": str(day), "total": len(out),
            "selected": selected, "quota": quota, "expected_top_n": expected,
            "quota_shortfall": max(0, expected - selected),
            "items": out[:max(1, min(limit, 200))]}


@router.get("/hermes/base-signals")
def base_signals(market: str = "CN", state: str | None = None, limit: int = 100):
    e = _db()
    with e.connect() as c:
        sql = "SELECT market, code, name, trade_date, data_date, strategy_id, signal_type, base_state, trigger_rule, trigger_price, entry_low, entry_high, stop_price, target_price, exit_rule, source FROM v2_base_signal WHERE market=:m"
        params = {"m": market.upper(), "n": max(1, min(limit, 300))}
        if state:
            sql += " AND base_state=:s"
            params["s"] = state.upper()
        sql += " ORDER BY trade_date DESC, code LIMIT :n"
        rows = c.execute(sa.text(sql), params).mappings().all()
    return {"read_only": True, "market": market.upper(), "count": len(rows),
            "items": [_clean(dict(r)) for r in rows]}


@router.get("/hermes/data-inventory")
def data_inventory():
    path = Path(r"C:\Temp\data_inventory.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "hermes.data_inventory_missing"})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "hermes.data_inventory_unreadable", "error": str(exc)[:120]})


@router.get("/hermes/strategy-list")
def strategy_list():
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"strategies": []}
    e = _db()
    with e.connect() as c:
        own = {r["strategy_id"]: dict(r) for r in c.execute(sa.text("SELECT * FROM v2_strategy_ownership")).mappings()}
        latest = {}
        for r in c.execute(sa.text("""
            SELECT strategy_id, max(report_date) AS report_date, count(*) AS reports
            FROM v2_selection_report GROUP BY strategy_id
        """)).mappings():
            latest[r["strategy_id"]] = {"report_date": str(r["report_date"]), "reports": r["reports"]}
    items = []
    for x in doc.get("strategies") or []:
        sid = x["id"]
        items.append({**_clean(own.get(sid, {})), "manifest": x, "output": latest.get(sid, {})})
    return {"read_only": True, "count": len(items), "items": items}


@router.get("/hermes/strategy/{strategy_id}")
def strategy_detail(strategy_id: str):
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"strategies": []}
    item = next((x for x in doc.get("strategies") or [] if x["id"] == strategy_id), None)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "hermes.strategy_not_found"})
    e = _db()
    with e.connect() as c:
        own = c.execute(sa.text("SELECT * FROM v2_strategy_ownership WHERE strategy_id=:s"), {"s": strategy_id}).mappings().first()
        versions = c.execute(sa.text("SELECT strategy_id,version_no,status,research_ref,created_at FROM v2_strategy_version WHERE strategy_id=:s ORDER BY created_at DESC"), {"s": strategy_id}).mappings().all()
        report = c.execute(sa.text("SELECT max(report_date) FROM v2_selection_report WHERE strategy_id=:s"), {"s": strategy_id}).scalar()
    return {"read_only": True, "manifest": item, "ownership": _clean(dict(own)) if own else None,
            "versions": [_clean(dict(v)) for v in versions], "latest_report": str(report) if report else None}


@router.get("/hermes/pool/today")
def pool_today():
    e = _db()
    with e.connect() as c:
        day = c.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        rows = c.execute(sa.text("""
            SELECT market, code, name, strategy_id, signal, score, status, data_date, selected_at, selection_reason, selected_by
            FROM v2_acad1_pool WHERE pool_date=:d ORDER BY market, code
        """), {"d": day}).mappings().all()
        base = {}
        try:
            for r in c.execute(sa.text("""
                SELECT DISTINCT ON (market, code) market, code, base_state, trigger_rule,
                       trigger_price, stop_price, target_price, entry_low, entry_high
                FROM v2_base_signal ORDER BY market, code, trade_date DESC
            """)).mappings().all():
                base[(r["market"], r["code"])] = dict(r)
        except Exception:  # noqa: BLE001
            base = {}
    items = []
    for r in rows:
        d = _clean(dict(r))
        b = base.get((r["market"], r["code"])) or {}
        d.update({k: _clean(v) for k, v in b.items() if k not in ("market", "code")})
        items.append(d)
    return {"read_only": True, "pool_date": str(day) if day else None,
            "count": len(items), "items": items}


@router.get("/hermes/review-queue")
def review_queue():
    e = _db()
    with e.connect() as c:
        day = c.execute(sa.text("SELECT max(pool_date) FROM v2_stock_research WHERE status='draft'")).scalar()
        rows = c.execute(sa.text("""
            SELECT pool_date, strategy_id, code, name, direction, status
            FROM v2_stock_research WHERE status='draft' AND pool_date=:d ORDER BY strategy_id, code
        """), {"d": day}).mappings().all()
    return {"read_only": True, "pool_date": str(day) if day else None,
            "count": len(rows), "items": [_clean(dict(r)) for r in rows]}


@router.get("/hermes/job-status")
def job_status():
    if not JOB_STATUS.exists():
        raise HTTPException(status_code=404, detail={"code": "hermes.job_not_reported"})
    try:
        d = json.loads(JOB_STATUS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "hermes.job_unreadable", "error": str(exc)[:120]})
    return {"read_only": True, "job": d}
