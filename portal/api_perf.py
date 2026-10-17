# -*- coding: utf-8 -*-
"""M4-P5 投研绩效看板只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/perf/board
    → perf_ledger 只读投影：账本规模、阵容分组后验（by_profile）、
    校准阈值、最近明细、intel 起草裁决（roster 相关 verdict）。
GET /api/perf/replay?ref=<run_id|trace_id|order_id|perf_key>
    → perf_chain_view 四环全链回放（影子→Paper→成交→绩效）。

只读纪律（沿 api_capability 判例）：mode=ro / 无缓存 / fail-visible——
账本未建返回 ledger_exists=false 空投影而非报错（观测面）；
replay NOT_FOUND 是合法回答非错误。
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_PLATFORM = _REPO_ROOT / "agent_platform"

# 校准阈值展示口径（与 engine/perf_ledger 常量同步——只读面不 import
# 引擎模块，避免门户进程承载引擎依赖链）
THRESHOLDS = {"min_samples": 5, "mean_ret_floor": -0.5}


def _perf_rows(root: Path = None):
    """perf_ledger 行只读读取（坏行容忍由引擎层负责）。

    root 缺省一律取模块级 _REPO_ROOT（pytest patch 单点生效判例，
    引擎默认 _AGENT_PLATFORM 是真仓常量不随 patch 动）。
    """
    root = root or _REPO_ROOT
    try:
        import sys
        sys.path.insert(0, str(_AGENT_PLATFORM))
        from engine.perf_ledger import load_perf_rows, ledger_path
        p = ledger_path(root)
        if not p.exists():
            return None
        return load_perf_rows(root)
    except Exception:  # noqa: BLE001
        return None


def _roster_verdicts(limit: int = 10):
    """intel 裁决池中 roster 相关条目（table_urn 前缀匹配，只读）。"""
    p = _AGENT_PLATFORM / "data" / "intel_verdicts.jsonl"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(e.get("table_urn", "")).startswith(
                    "agent_platform/perf_ledger"):
                out.append({
                    "verdict_id": e.get("verdict_id"),
                    "decision": e.get("decision"),
                    "break_kind": e.get("break_kind"),
                    "impact": e.get("impact", []),
                    "rationale": e.get("rationale", ""),
                    "job_run_id": e.get("job_run_id"),
                    "content_hash": (e.get("content_hash") or "")[:16],
                })
    return out[-limit:]


def build_perf_board(limit_recent: int = 20, root: Path = None) -> dict:
    rows = _perf_rows(root)
    if rows is None:
        return {"ledger_exists": False, "total": 0,
                "profile_stats": {}, "thresholds": THRESHOLDS,
                "recent": [], "roster_verdicts": _roster_verdicts()}
    try:
        import sys
        sys.path.insert(0, str(_AGENT_PLATFORM))
        from engine.perf_ledger import by_profile
        stats = by_profile(rows)
    except Exception:  # noqa: BLE001
        stats = {}
    recent = sorted(rows, key=lambda r: (r.get("date", ""),
                                         r.get("symbol", "")))[-limit_recent:]
    recent.reverse()
    return {
        "ledger_exists": True,
        "total": len(rows),
        "profile_stats": stats,
        "thresholds": THRESHOLDS,
        "recent": [{
            "perf_key": r.get("perf_key"), "date": r.get("date"),
            "symbol": r.get("symbol"), "run_id": r.get("run_id"),
            "score": r.get("score"), "ret_pct": r.get("ret_pct"),
            "horizon_days": r.get("horizon_days"),
            "recommendation": r.get("recommendation"),
            "stance": r.get("stance"),
            "dk_regime": r.get("dk_regime"),
            "dk_profile": r.get("dk_profile"),
        } for r in recent],
        "roster_verdicts": _roster_verdicts(),
    }


def build_perf_replay(target: str, root: Path = None) -> dict:
    target = (target or "").strip()
    if not target:
        return {"verdict": "INVALID", "matches": [], "perf_rows": [],
                "profile_stats": {}, "findings": ["ref 参数必填"]}
    root = root or _REPO_ROOT
    try:
        import sys
        sys.path.insert(0, str(_AGENT_PLATFORM))
        from adapters.trace_link import LinkStore
        from engine.perf_ledger import perf_chain_view, ledger_path
        links_path = (root or _REPO_ROOT) / "agent_platform" / "data" / \
            "trace" / "trace_links.jsonl"
        if not links_path.exists():
            return {"verdict": "NOT_FOUND", "matches": [],
                    "perf_rows": [], "profile_stats": {},
                    "findings": ["链账本未建"]}
        store = LinkStore(links_path)
        return perf_chain_view(store, target, root or _REPO_ROOT)
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "ERROR", "matches": [], "perf_rows": [],
                "profile_stats": {}, "findings": ["回放异常: %s" % exc]}


class Handler:
    """/api/perf/* 只读端点。"""

    def _perf_board(self, qs):
        raw = (qs.get("limit") or ["20"])[0]
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            # fail-visible：非数字不吞成默认值（静默降级=参数白名单旁路）
            self._json({"error": "limit 须为整数"}, 400)
            return
        if limit < 1 or limit > 200:
            self._json({"error": "limit 须在 1..200"}, 400)
            return
        self._json(build_perf_board(limit_recent=limit))

    def _perf_replay(self, qs):
        ref = (qs.get("ref") or [""])[0].strip()
        if not ref:
            self._json({"error": "ref 必填（run_id/trace_id/order_id/"
                                "perf_key 任一锚点值）"}, 400)
            return
        self._json(build_perf_replay(ref))
