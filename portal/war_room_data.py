# -*- coding: utf-8 -*-
"""量化作战台实时数据聚合（2026-10-30 WO-WARROOM-LIVE）。

一个只读接口把 D1 研究、ACAD1 池、quant paper、人工确认、盯盘摘要聚合起来；
前端优先 fetch，失败时回落到页面内静态快照。不接实盘。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

PORTAL = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PORTAL)
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import d1_data  # noqa: E402
import paper_data  # noqa: E402
import pool_data  # noqa: E402
import sim_confirm  # noqa: E402
import ifs_envelope  # noqa: E402  IFS F1d（WO-IFS-WARROOM-ENVELOPE-20261104）

try:  # 与 portal_api 原样一致的导入兜底（包内/脚本两种入口）
    from safe_urlopen import safe_urlopen  # noqa: E402
except ImportError:  # pragma: no cover
    from portal.safe_urlopen import safe_urlopen  # noqa: E402

STRATEGIES = [
    {"id": "chanlun", "name": "缠论策略", "color": "#d6a94a", "desc": "缠论三类买卖点 / ST1"},
    {"id": "ninedim", "name": "九维选股", "color": "#58a6ff", "desc": "多维漏斗打分"},
    {"id": "weak", "name": "弱于板块", "color": "#bc8cff", "desc": "相对板块走弱规避"},
    {"id": "qpzm", "name": "青萍之末", "color": "#3fb9a6", "desc": "早期异动捕捉"},
    {"id": "linkage", "name": "三线联动", "color": "#f0883e", "desc": "多周期/多池联动"},
    {"id": "double_knife", "name": "量子双刀", "color": "#ff7b72", "desc": "双策略合流"},
    {"id": "emotion", "name": "打板情绪", "color": "#ff4d6d", "desc": "情绪周期"},
]
WATCH_ADMIN = "http://127.0.0.1:8789/api/watch-admin/snapshot"
_WATCH_CACHE = {"ts": 0.0, "data": None}


def _watch_summary(armed):
    import time
    out = {"armed": armed, "verified_sources": 17, "unverified": 2,
           "admin_url": "http://127.0.0.1:8789/watch-admin", "source": "d1_handoff"}
    cached = _WATCH_CACHE.get("data")
    if cached is not None and time.time() - _WATCH_CACHE.get("ts", 0) < 60:
        out.update(cached)
        return out
    try:
        with safe_urlopen(WATCH_ADMIN, timeout=6) as r:
            out["snapshot"] = json.loads(r.read())
        out["source"] = "watch_admin"
    except Exception as exc:  # noqa: BLE001 8789 未启不阻塞页面
        out["error"] = str(exc)[:120]
    _WATCH_CACHE["data"] = {"source": out["source"]}
    _WATCH_CACHE["ts"] = time.time()
    if "snapshot" in out:
        _WATCH_CACHE["data"]["snapshot"] = out["snapshot"]
    return out


def war_room_snapshot():
    pool = pool_data.pool_snapshot()
    d1 = d1_data.d1_snapshot()
    paper = paper_data.paper_snapshot()
    try:
        confirmations = sim_confirm.list_payload("pending")
    except Exception:  # noqa: BLE001
        confirmations = {"ok": False, "items": []}
    d1map = {x.get("code"): x for x in d1.get("items") or []}
    poolmap = {str(x.get("code")): x for x in pool.get("items") or []}
    items = []
    for code, x in d1map.items():
        p = poolmap.get(str(code)) or {}
        sources = p.get("sources") or x.get("sources") or []
        primary = sources[0] if sources else "other"
        item = dict(x)
        item.update({"sources": sources, "primary": primary,
                     "industry": p.get("industry") or x.get("industry") or "",
                     "tier": p.get("tier") or x.get("tier") or ""})
        items.append(item)
    # 池内 pending 票也展示（D1 未研究）
    for code, p in poolmap.items():
        if code not in d1map:
            items.append({
                "code": code, "name": p.get("name"), "industry": p.get("industry") or "",
                "tier": p.get("tier") or "", "sources": p.get("sources") or [],
                "primary": (p.get("sources") or ["other"])[0],
                "recommendation": None, "confidence": None, "would_buy": False,
                "can_buy": False, "action": "pending_research",
                "buy_zone": None, "watch_trigger": None, "stop_loss": None,
                "target1": None, "target2": None, "handoff": {"to": "D1", "status": "pending"},
                "bull": "", "bear": "", "sentiment": "", "fundamental": "",
                "rationale": "", "data_date": p.get("data_date"), "stale": p.get("stale"),
            })
    counts = {s["id"]: sum(1 for x in items if s["id"] in (x.get("sources") or []))
              for s in STRATEGIES}
    armed = sum(1 for x in items if (x.get("handoff") or {}).get("status") == "armed")
    return {
        "ok": True, "mode": "war_room_live",
        "date": d1.get("date") or pool.get("date"),
        "data_date": d1.get("data_date") or pool.get("data_date"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "d1_summary": d1.get("summary") or {},
        "pool_summary": pool.get("summary") or {},
        "strategies": STRATEGIES,
        "strategy_counts": counts,
        "items": items,
        "paper": paper,
        "watch": _watch_summary(armed),
        "confirmations": {"pending": len(confirmations.get("items") or []),
                          "ok": bool(confirmations.get("ok", True))},
        "links": {
            "d1": "http://127.0.0.1:8734/d1_research.html",
            "quant_paper": "http://127.0.0.1:8734/quant_paper.html",
            "legacy_paper": "http://127.0.0.1:8734/paper",
            "watch_admin": "http://127.0.0.1:8789/watch-admin",
            "confirm": "http://127.0.0.1:8734/sim_confirm.html",
        },
        "trading_permitted": False,
    }


def snapshot_envelope(handler):
    """IFS F1d：/api/war-room/snapshot 统一 envelope 响应，返回 (payload, http_status)。

    只读聚合，复用 ``portal/ifs_envelope.py``（F1b）：
    - 成功：ok/schema_version=war_room/v1/request_id/source/data/warnings + 旧顶层字段；
    - 失败：fail-visible error/v1（不伪造快照；前端回落页面内静态快照）。

    schema ``war_room/v1`` 为 F1d 本地契约（registry 暂无 war-room 条目，待登记）。
    """
    try:
        return (
            ifs_envelope.ok_payload(handler, "war_room.snapshot.get", war_room_snapshot()),
            200,
        )
    except Exception as exc:  # noqa: BLE001 单接口失败不拖垮页面（前端回落静态快照）
        return (
            ifs_envelope.error_payload(
                handler,
                "war_room.snapshot.get",
                {"ok": False, "error": "war_room_snapshot_failed",
                 "message": str(exc)[:300]},
                500,
            ),
            500,
        )
