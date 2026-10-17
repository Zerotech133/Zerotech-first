# -*- coding: utf-8 -*-
"""M1 统一投影只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/unified/projection
    → agent_platform/adapters/unified_projection.build_unified_report()
    全部只读；60s 进程内缓存；门户 UI 拿统一视图（六域 workitem/
    审批/越门审计/任务事件摘要），不再各自直读事实源文件。

GET /api/unified/replay?target=gzh:xxx|trace=...|idem=...
    → agent_platform/adapters/replay_query.replay()
    跨账本串线回放（events + idempotency + approvals + 域现状）。
    事件流内过滤分页走 /api/platform/replay（event_replay mixin），
    两者互补勿混用。

M2 读侧四端点（全部只读、无写路径、低频直读不缓存）：
GET /api/unified/evidence?domain=&limit=  → 证据仓 stats + 明细
GET /api/unified/knowledge                → 候选池 + 越门审计
GET /api/unified/skills                   → Skill 目录 + 审计
GET /api/unified/usage?days=              → 统一使用账本报告

审批注册表清单/过滤走 /api/platform/approvals（api_approvals mixin，
并行线交付）——本 mixin 不重复覆盖，UI 页 /approvals 消费之。
"""

from __future__ import annotations

import json
import time
import urllib.parse as _urlparse
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CACHE = {"t": 0.0, "data": None}
_CACHE_TTL = 60  # 秒；投影层全部只读，短暂缓存不影响正确性


def _ap_import(rel_module: str, attr: str):
    """agent_platform 包内导入；portal 直跑时回退 sys.path 注入。"""
    try:
        import agent_platform  # noqa: F401
        mod = __import__("agent_platform.%s" % rel_module,
                         fromlist=[attr])
        return getattr(mod, attr)
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "agent_platform"))
        mod = __import__(rel_module, fromlist=[attr])
        return getattr(mod, attr)


class Handler:
    """统一投影 mixin（模块名绑定：portal_api 里 import api_unified）。"""

    def _unified_projection(self):
        build_unified_report = _ap_import(
            "adapters.unified_projection", "build_unified_report")
        now = time.time()
        if _CACHE["data"] is None or now - _CACHE["t"] > _CACHE_TTL:
            _CACHE["data"] = build_unified_report(_REPO_ROOT)
            _CACHE["t"] = now
        self._json(_CACHE["data"])

    def _unified_replay(self, qs: str):
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        trace = unquote(params.get("trace", ""))
        idem = unquote(params.get("idem", ""))
        target = unquote(params.get("target", ""))
        replay = _ap_import("adapters.replay_query", "replay")
        try:
            report = replay(_REPO_ROOT, trace_id=trace,
                            idempotency_key=idem, target_id=target)
        except ValueError as exc:
            self._json({"error": "invalid_query", "message": str(exc)}, 400)
            return
        self._json(report)

    # ------------------------------------------------------------------
    # M2 读侧四端点（全只读；fail loud 500；参数白名单）
    # ------------------------------------------------------------------
    @staticmethod
    def _qs_int(params: dict, name: str, default: int,
                lo: int, hi: int) -> int:
        raw = (params.get(name) or [None])[0]
        if raw in (None, ""):
            return default
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise ValueError("%s 必须是整数" % name)
        if not (lo <= v <= hi):
            raise ValueError("%s 超界（%d~%d）" % (name, lo, hi))
        return v

    def _unified_evidence(self, qs: str):
        params = _urlparse.parse_qs(qs or "", keep_blank_values=True)
        try:
            limit = self._qs_int(params, "limit", 50, 1, 500)
        except ValueError as exc:
            self._json({"error": "invalid_query", "message": str(exc)}, 400)
            return
        domain = (params.get("domain") or [None])[0]
        EvidenceStore = _ap_import("engine.evidence", "EvidenceStore")
        db = _REPO_ROOT / "agent_platform" / "data" / "evidence" / "evidence.db"
        if not db.exists():
            self._json({"ledger_exists": False, "stats": {},
                        "items": [], "note": "证据仓尚未建立"})
            return
        store = EvidenceStore(db)
        try:
            items = [asdict(e) for e in store.list(domain=domain,
                                                   limit=limit)]
            out = {"ledger_exists": True, "stats": store.stats(),
                   "count": len(items), "items": items}
        finally:
            store.close()
        self._json(out)

    def _unified_knowledge(self):
        # root 传 None：knowledge_wiring 默认基准 = 其所在 agent_platform
        # 目录（与真实仓库一致），勿传 _REPO_ROOT（会落到仓库根 data/）
        load_pending = _ap_import("domains.knowledge_wiring", "load_pending")
        audit_report = _ap_import("domains.knowledge_wiring", "audit_report")
        pending = load_pending(None)
        audit = audit_report(None)
        self._json({"pending_count": len(pending), "pending": pending,
                    "audit": audit, "read_only": True})

    def _unified_skills(self):
        load_skills = _ap_import("domains.skill_wiring", "_load")
        audit_report = _ap_import("domains.skill_wiring", "audit_report")
        skills = load_skills(None)
        self._json({"skill_total": len(skills), "skills": skills,
                    "audit": audit_report(None), "read_only": True})

    def _unified_usage(self, qs: str):
        params = _urlparse.parse_qs(qs or "", keep_blank_values=True)
        try:
            days = self._qs_int(params, "days", 0, 0, 365) or None
        except ValueError as exc:
            self._json({"error": "invalid_query", "message": str(exc)}, 400)
            return
        build_usage_report = _ap_import("adapters.usage_ledger",
                                        "build_usage_report")
        self._json(build_usage_report(None, days=days))
