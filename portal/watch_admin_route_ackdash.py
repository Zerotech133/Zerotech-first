# -*- coding: utf-8 -*-
"""Watch-admin read-only view: 单A — 实时 ACK / 多 Agent 投票 / 用量看板.

Hard constraints (inherited from the watch-admin project):
  * Read-only. No business state is written by this module or this process.
  * Fail-visible: when a source is absent, the panel reports it explicitly
    (state=unknown, error_code="source_missing", source_quality="unverified")
    and NEVER fabricates values.
  * Data sources are strictly local read-only files. No Feishu / no trade /
    no notification / no 10.205.0.118 / no 8091. The already-approved 8097
    read-only GET path is intentionally NOT used here because 8097 exposes the
    trade pool (active/pending/health), which is unrelated to these three
    panels; claiming it as a source would be fabrication.

This module is appended to watch_admin_server._FEATURE_MODULES; the server
dispatches GET /api/watch-admin/ackdash -> _build_ackdash(query).
"""
from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PORTAL_ROOT = _HERE.parent  # portal/ -> 老田的AI助手/

# Designated local read-only source probes (all OPTIONAL; absence => 待补).
# Default paths are conservative; if none exist the panel honestly reports
# "source_missing" rather than inventing data.
_LOCAL_PROBES = {
    "ack": [  # 实时 ACK：告警/信号的签收状态
        _PORTAL_ROOT / "portal" / "data" / "alert_ack.json",
        _PORTAL_ROOT / "portal" / "data" / "watch_ack_ledger.json",
    ],
    "vote": [  # 多 Agent 投票：模型对比评审结论
        _PORTAL_ROOT / "portal" / "data" / "model_vote.json",
        _PORTAL_ROOT / "agent_platform" / "data" / "review_votes.json",
    ],
    "usage": [  # 用量看板：API/计算用量
        _PORTAL_ROOT / "portal" / "data" / "usage_stats.json",
        _PORTAL_ROOT / "logs" / "usage.json",
    ],
}


def _read_local_json(probes):
    """Return (data, found_path) for the first existing, readable local probe.

    Returns (None, None) if none exist or all are unreadable — never raises,
    never fabricates.
    """
    for p in probes:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")), str(p)
        except Exception:
            # Corrupt/unreadable -> treat as missing; do NOT invent data.
            continue
    return None, None


def _source_block(name, data, found_path):
    """Build a fail-visible source block for one metric."""
    if data is None:
        return {
            "name": name,
            "state": "unknown",
            "error_code": "source_missing",
            "source_quality": "unverified",
            "note": "本地无该数据源，未编造（待补）。",
            "data": None,
            "source": None,
        }
    return {
        "name": name,
        "state": "healthy",
        "error_code": None,
        "source_quality": "verified_local",
        "note": "本地只读文件读取成功（仅 SELECT，不写回）。",
        "data": data,
        "source": found_path,
    }


def _build_ackdash(query=None):
    """Aggregate the three read-only panels, fail-visible for missing sources.

    `query` is the parsed query dict from the server (unused here, accepted for
    contract compatibility).
    """
    ack_data, ack_path = _read_local_json(_LOCAL_PROBES["ack"])
    vote_data, vote_path = _read_local_json(_LOCAL_PROBES["vote"])
    usage_data, usage_path = _read_local_json(_LOCAL_PROBES["usage"])
    return {
        "schema": "watch-admin/ackdash/v1",
        "realm": "read-only-internal",
        "note": ("单A 只读视图：实时 ACK / 多 Agent 投票 / 用量看板。"
                 "纯只读、不写库、不触飞书/交易/通知/10.205.0.118/8091。"),
        "panels": {
            "ack": _source_block("实时 ACK（告警/信号签收）", ack_data, ack_path),
            "vote": _source_block("多 Agent 投票（模型对比评审）", vote_data, vote_path),
            "usage": _source_block("用量看板（API/计算用量）", usage_data, usage_path),
        },
    }


API_ROUTES = {
    "/api/watch-admin/ackdash": _build_ackdash,
}
