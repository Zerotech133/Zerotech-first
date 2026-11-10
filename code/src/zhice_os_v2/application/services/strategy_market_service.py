# -*- coding: utf-8 -*-
"""策略市场应用服务：sidecar 执行状态、张松研究条目。

路由只做 HTTP/鉴权；本服务负责策略市场数据组装。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

RESEARCH_FILE = Path(__file__).resolve().parents[4] / "config" / "strategy_market_research.json"
GATE_FILE = Path(__file__).resolve().parents[4] / "config" / "strategy_gate_matrix.json"
FOUR_GATE_FILE = Path(__file__).resolve().parents[4] / "config" / "strategy_four_gate_results.json"


def _gate_map() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(GATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(x.get("strategy_id")): dict(x) for x in payload.get("items") or [] if isinstance(x, dict)}


def _four_gate_map() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(FOUR_GATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(k): dict(v) for k, v in (payload.get("strategies") or {}).items()
            if isinstance(v, dict)}


def load_research_items(path: Path | None = None) -> list[dict[str, Any]]:
    """读取已审核的张松研究目录，并合并 G1-G8 严格裁决。

    市场目录的 decision 保留为 market_decision；status/decision 采用严格裁决，
    冲突项带 verdict_conflict=true，避免把 needs-more/close 混淆。
    """
    p = path or RESEARCH_FILE
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        items = [dict(x) for x in payload.get("items") or [] if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        return []
    gates = _gate_map()
    four = _four_gate_map()
    for x in items:
        g = gates.get(str(x.get("strategy_id")))
        if not g:
            continue
        strict = str(g.get("strict_verdict") or "")
        market = str(x.get("decision") or "")
        x["strict_verdict"] = strict
        x["market_decision"] = market
        x["verdict_conflict"] = bool(g.get("verdict_conflict"))
        x["gate_summary"] = {
            "G1": (g.get("G1") or {}).get("status"),
            "G2": (g.get("G2") or {}).get("status"),
            "G3": (g.get("G3") or {}).get("status"),
            "G4": (g.get("G4") or {}).get("status"),
            "G5": (g.get("G5") or {}).get("status"),
            "G6": (g.get("G6") or {}).get("status"),
            "G7": (g.get("G7") or {}).get("status"),
            "G8": (g.get("G8") or {}).get("status"),
        }
        if strict == "close":
            x["status"] = "RESEARCH_CLOSED"
            x["decision"] = "关闭"
        elif strict == "needs-more":
            x["status"] = "RESEARCH_NEEDS_MORE"
            x["decision"] = "待补验"
        x["tradable"] = False
        x["lifecycle_status"] = "research"
        fg = four.get(str(x.get("strategy_id"))) or {}
        if fg:
            x["four_gate_status"] = fg.get("verdict") or "not-evaluated"
            x["four_gate_report"] = fg.get("report")
            x["four_gate_pre_registration"] = fg.get("pre_registration")
            x["four_gate_scope"] = fg.get("role")
    return items


def build_sidecar_items(
    market: str | None,
    own: dict[str, dict[str, Any]],
    *,
    session: Any,
    rows: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """把 sidecar 注册行与真实日线/选股池状态合并，供策略市场展示。

    session 由路由/应用层注入，服务本身不直接依赖 infrastructure。
    """
    if rows is None:
        rows = session.execute(text(
            "SELECT strategy_id, display_name, status, owner, note, data_date, source_type, health "
            "FROM v2_strategy_sidecar ORDER BY strategy_id"
        )).mappings().all()
    latest = dict(session.execute(text(
        "SELECT strategy_id, max(data_date) FROM v2_strategy_daily GROUP BY strategy_id"
    )).all())
    counts = dict(session.execute(text(
        "SELECT strategy_id, count(*) FROM v2_strategy_daily GROUP BY strategy_id"
    )).all())
    pool = dict(session.execute(text(
        "SELECT x.strategy_id, count(*) FROM v2_selection_pool p "
        "JOIN LATERAL (SELECT unnest(string_to_array(p.source_strategies, ',')) AS strategy_id) x ON true "
        "WHERE p.pool_date=(SELECT max(pool_date) FROM v2_selection_pool) "
        "GROUP BY x.strategy_id"
    )).all())

    out: list[dict[str, Any]] = []
    for r in rows:
        if market and market != "CN":
            continue
        sid = r["strategy_id"]
        dd = latest.get(sid) or r.get("data_date")
        out.append({
            "strategy_id": sid,
            "strategy_name": r["display_name"],
            "description": r["note"] or "研究院研究策略（sidecar）",
            "market": "CN",
            "strategy_type": "SIDECAR",
            "status": (r["status"] or "live").upper(),
            "is_builtin": False,
            "editable": False,
            "tradable": False,
            "implemented": bool(dd),
            "execution_status": (
                "已接入选股池" if pool.get(sid)
                else ("已接入（当日无信号）" if sid == "dk" and dd else ("已导入未入池" if dd else "未接入"))
            ),
            "data_date": str(dd) if dd else None,
            "latest_signal_count": int(counts.get(sid) or 0),
            "pool_count": int(pool.get(sid) or 0),
            "owner_user_id": r["owner"],
            "research_owner": (own.get(sid) or {}).get("research_owner") or r["owner"],
            "validation_owner": (own.get(sid) or {}).get("validation_owner"),
            "data_owner": (own.get(sid) or {}).get("data_owner"),
            "ops_owner": (own.get(sid) or {}).get("ops_owner"),
            "lifecycle_status": (own.get(sid) or {}).get("lifecycle_status") or (r["status"] or "live").lower(),
            "next_review": str((own.get(sid) or {}).get("next_review") or ""),
            "version_count": 0,
            "versions": [],
            "is_sidecar": True,
        })
    return out
