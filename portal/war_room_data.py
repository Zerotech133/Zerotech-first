# -*- coding: utf-8 -*-
"""量化作战台实时数据聚合（2026-10-30 WO-WARROOM-LIVE）。

一个只读接口把 D1 研究、ACAD1 池、quant paper、人工确认、盯盘摘要聚合起来；
前端优先 fetch，失败时回落到页面内静态快照。不接实盘。
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
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
# 2026-11-15 WO-INV-WARROOM-FAST：原 6s 串行 → 1.5s 有界（8789 内 8098 已改缓存+并行）。
WATCH_TIMEOUT = 1.5
_WATCH_CACHE = {"ts": 0.0, "data": None}
_WATCH_TTL = 60.0
# 整快照进程内缓存：聚合口径 15–20s 足够新，避免每次点击都重跑全部数据源。
_SNAPSHOT_CACHE = {"ts": 0.0, "data": None}
SNAPSHOT_TTL = 20.0
# PG 可用性探测（60s 缓存）：PG 不可达时让池/研究两源走投影，不再每次等 3s 超时。
_PG_PROBE = {"ts": 0.0, "up": None}
PG_PROBE_TTL = 60.0
# 并行取回的 8789 结果（供 _watch_summary 单参签名读取；None=None 表示未取）。
_WATCH_LAST = {"snap": None, "err": None}


def _pg_guard():
    """PG 端口探测；不通时临时置 ACAD1_POOL_PG_DISABLE=1（语义等同 PG 不可用）。

    原本每次 war_room 请求都会在 pool/d1 两处各等 3s 连接超时（实测 8.2s 的主因）。
    """
    import socket
    import time

    now = time.time()
    if _PG_PROBE["up"] is not None and now - _PG_PROBE["ts"] < PG_PROBE_TTL:
        return _PG_PROBE["up"]
    up = False
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=0.3):
            up = True
    except OSError:
        up = False
    _PG_PROBE["ts"] = now
    _PG_PROBE["up"] = up
    if up:
        os.environ.pop("ACAD1_POOL_PG_DISABLE", None)
    else:
        os.environ["ACAD1_POOL_PG_DISABLE"] = "1"
    return up


def _watch_fetch():
    """取 8789 盯盘中台 snapshot（成功结果 60s 缓存）；返回 (snapshot|None, error|None)。"""
    import time

    now = time.time()
    if _WATCH_CACHE["data"] is not None and now - _WATCH_CACHE["ts"] < _WATCH_TTL:
        return _WATCH_CACHE["data"], None
    snap, err = None, None
    try:
        with safe_urlopen(WATCH_ADMIN, timeout=WATCH_TIMEOUT) as r:
            snap = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 8789 未启/超时不阻塞页面
        err = str(exc)[:120]
    if snap is not None:
        _WATCH_CACHE["data"] = snap
        _WATCH_CACHE["ts"] = now
    _WATCH_LAST["snap"], _WATCH_LAST["err"] = snap, err
    return snap, err


def _confirm_pending():
    try:
        return sim_confirm.list_payload("pending")
    except Exception:  # noqa: BLE001
        return {"ok": False, "items": []}


def _watch_summary(armed):
    """盯盘摘要。签名保持单参（外部/测试按私有一参调用）；并行取回的结果走 _WATCH_LAST。"""
    out = {"armed": armed, "verified_sources": 17, "unverified": 2,
           "admin_url": "http://127.0.0.1:8789/watch-admin", "source": "d1_handoff"}
    snap, err = _WATCH_LAST["snap"], _WATCH_LAST["err"]
    if snap is None and err is None:
        snap, err = _watch_fetch()
    if snap is not None:
        out["snapshot"] = snap
        out["source"] = "watch_admin"
    else:
        out["error"] = err or "unavailable"
    return out


def war_room_snapshot(use_cache=True):
    """作战台聚合快照（只读）。

    2026-11-15 WO-INV-WARROOM-FAST：
    - 5 个数据源并行（原串行，pool/d1 各自等 PG 3s 超时 → 实测 8.2s）；
    - PG 端口不通时临时禁用 PG 池读取（走投影），不再每次白等；
    - 整快照进程内缓存 ``SNAPSHOT_TTL``（默认 20s）；``use_cache=False`` 强制重算。
    """
    import time

    now = time.time()
    if use_cache and _SNAPSHOT_CACHE["data"] is not None \
            and now - _SNAPSHOT_CACHE["ts"] < SNAPSHOT_TTL:
        return _SNAPSHOT_CACHE["data"]
    _pg_guard()
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_pool = executor.submit(pool_data.pool_snapshot)
        f_d1 = executor.submit(d1_data.d1_snapshot)
        f_paper = executor.submit(paper_data.paper_snapshot)
        f_conf = executor.submit(_confirm_pending)
        f_watch = executor.submit(_watch_fetch)
        pool = f_pool.result()
        d1 = f_d1.result()
        paper = f_paper.result()
        confirmations = f_conf.result()
        wsnap, werr = f_watch.result()
    _WATCH_LAST["snap"], _WATCH_LAST["err"] = wsnap, werr
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
    payload = {
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
    _SNAPSHOT_CACHE["ts"] = now
    _SNAPSHOT_CACHE["data"] = payload
    return payload


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
