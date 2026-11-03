# -*- coding: utf-8 -*-
"""Paper 入口策略（IFS v1 F4，2026-11-03 WO-IFS-PAPER-20261103）。

**唯一 A 路径**（确认 → paper 执行）::

    POST /api/sim/confirm  →  agent_platform/trading_bot_flow.decide/auto_execute
                               →  agent_platform/paper/paper_book（模拟账）

只有 A 路径会走「人工确认 + risk_gate + paper_book + trade receipt（四 ID 链）」。
B/C 两个旧入口（``/api/paper_order``、``/api/paper_direct_open``）保留可用，但
标注为 **legacy**：它们直连 K-STOCK 沙盘/CLI 子进程，**不接 D1 交棒链**、不产生
confirmation / decision_id / handoff_id / watch_plan_id / trade_id 四 ID 链。

本模块是纯声明（无 IO、无网络、无写盘），给 API 层/页面/审计统一口径：
- ``describe()`` 整表；``entry_for(path)`` 单路径；``legacy_marker(path)``；
- ``annotate(payload, path)`` 给响应附加 ``entry`` 口径（不覆盖业务字段）；
- ``entry_headers(path)`` 供 HTTP 层加 ``X-Paper-Entry`` 头。
"""

from __future__ import annotations

SCHEMA = "paper_entry_policy/v1"

#: 唯一 A 路径的稳定标识（写入 confirmation/receipt，用于对账）。
UNIQUE_A_PATH = "trading_bot_flow+/api/sim/confirm"

A_ENTRY = {
    "path": "A",
    "kind": "confirmation_gate",
    "api": "POST /api/sim/confirm",
    "module": "agent_platform.trading_bot_flow",
    "execute": "agent_platform.paper.paper_book",
    "creates_confirmation": True,
    "requires_human_gate": True,
    "d1_chain": True,
    "id_chain": ("decision_id", "handoff_id", "watch_plan_id",
                 "confirmation_id", "trade_id"),
    "legacy": False,
    "paper_only": True,
    "trading_permitted": False,
    "reason": "IFS v1 唯一 A 路径：确认→paper，四 ID 链可回写",
}

LEGACY_B = {
    "path": "B",
    "kind": "legacy_direct_paper_db",
    "api": "POST /api/paper_order",
    "module": "portal.api_manager_portal._api_paper_order",
    "execute": "K-STOCK paper cli.py order（legacy 沙盘）",
    "creates_confirmation": False,
    "requires_human_gate": False,
    "d1_chain": False,
    "id_chain": (),
    "legacy": True,
    "legacy_label": "legacy",
    "replacement": UNIQUE_A_PATH,
    "paper_only": True,
    "trading_permitted": False,
    "reason": "K-STOCK v0.2 legacy 直连沙盘下单：无人工确认/无四 ID 链，不接 D1；"
              "新确认请走 A 路径",
}

LEGACY_C = {
    "path": "C",
    "kind": "legacy_direct_open",
    "api": "POST /api/paper_direct_open",
    "module": "portal.api_manager_portal._api_paper_direct_open",
    "execute": "K-STOCK paper cli.py order（legacy 沙盘，source=direct_lightweight）",
    "creates_confirmation": False,
    "requires_human_gate": False,
    "d1_chain": False,
    "id_chain": (),
    "legacy": True,
    "legacy_label": "legacy",
    "replacement": UNIQUE_A_PATH,
    "paper_only": True,
    "trading_permitted": False,
    "reason": "K-STOCK-137f 轻量直订 legacy 入口：策略市场直接建仓，无四 ID 链，"
              "不接 D1；新确认请走 A 路径",
}

#: 旧入口注册表（键=API path；不带 query）。
LEGACY_ENTRIES = {
    "/api/paper_order": LEGACY_B,
    "/api/paper_direct_open": LEGACY_C,
}

#: 所有已登记入口（A + legacy）。
ENTRIES = {
    "/api/sim/confirm": A_ENTRY,
    "/api/paper_order": LEGACY_B,
    "/api/paper_direct_open": LEGACY_C,
}

ENTRY_HEADER = "X-Paper-Entry"
ENTRY_HEADER_A = "A"
ENTRY_HEADER_LEGACY = "legacy"


class PaperEntryError(ValueError):
    """入口声明错误（未知 path / 非 paper-only 等，调用方 fail-closed）。"""


def _norm_path(path):
    if not isinstance(path, str) or not path.strip():
        raise PaperEntryError("path 须为非空字符串: %r" % (path,))
    clean = path.strip().split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    return clean


def entry_for(path):
    """path → 入口声明（A/legacy），未知返回 None（调用方按 404 处理）。"""
    return ENTRIES.get(_norm_path(path))


def is_legacy(path):
    """是否 legacy 入口（B/C）；未知 path 返回 False（不猜）。"""
    entry = entry_for(path)
    return bool(entry and entry.get("legacy"))


def is_unique_a_path(path):
    """是否唯一 A 路径。"""
    entry = entry_for(path)
    return bool(entry and entry.get("path") == "A" and not entry.get("legacy"))


def legacy_marker(path):
    """legacy 标注（非 legacy/未知 → None）。"""
    if not is_legacy(path):
        return None
    entry = entry_for(path)
    return {
        "entry": "legacy",
        "legacy": True,
        "path": entry.get("path"),
        "api": entry.get("api"),
        "d1_chain": False,
        "id_chain": [],
        "replacement": entry.get("replacement") or UNIQUE_A_PATH,
        "reason": entry.get("reason") or "",
        "paper_only": True,
        "trading_permitted": False,
    }


def entry_headers(path):
    """HTTP 头口径：A → ``X-Paper-Entry: A``；legacy → ``legacy``；未知 → {}。"""
    entry = entry_for(path)
    if entry is None:
        return {}
    return {ENTRY_HEADER: ENTRY_HEADER_A if not entry.get("legacy")
            else ENTRY_HEADER_LEGACY}


def annotate(payload, path):
    """给响应 payload 附加 ``entry`` 只读口径（返回新 dict，不改入参）。

    - A 路径：``entry`` 为 A 声明（``legacy=false``、五 ID 链）；
    - legacy：额外 ``legacy=true`` + ``replacement``，方便页面/审计显式区分；
    - 未知 path：不附加（调用方自行 404）。
    """
    if not isinstance(payload, dict):
        raise PaperEntryError("payload 须为 dict: %r" % (payload,))
    entry = entry_for(path)
    if entry is None:
        return dict(payload)
    out = dict(payload)
    out["entry"] = {
        "path": entry["path"],
        "kind": entry["kind"],
        "api": entry["api"],
        "legacy": bool(entry.get("legacy")),
        "legacy_label": entry.get("legacy_label") if entry.get("legacy") else None,
        "d1_chain": bool(entry.get("d1_chain")),
        "id_chain": list(entry.get("id_chain") or ()),
        "paper_only": True,
        "trading_permitted": False,
    }
    if entry.get("legacy"):
        out["legacy"] = True
        out["replacement"] = entry.get("replacement") or UNIQUE_A_PATH
        out["entry_note"] = entry.get("reason") or ""
    else:
        out["legacy"] = False
    return out


def describe():
    """入口口径总表（只读；供审计/页面/API 元信息）。"""
    return {
        "schema": SCHEMA,
        "unique_a_path": UNIQUE_A_PATH,
        "entries": [dict(A_ENTRY), dict(LEGACY_B), dict(LEGACY_C)],
        "legacy_apis": sorted(LEGACY_ENTRIES),
        "paper_only": True,
        "trading_permitted": False,
        "real_trading": False,
        "migrates_books": False,
    }


__all__ = [
    "SCHEMA",
    "UNIQUE_A_PATH",
    "A_ENTRY",
    "LEGACY_B",
    "LEGACY_C",
    "LEGACY_ENTRIES",
    "ENTRIES",
    "ENTRY_HEADER",
    "ENTRY_HEADER_A",
    "ENTRY_HEADER_LEGACY",
    "PaperEntryError",
    "entry_for",
    "is_legacy",
    "is_unique_a_path",
    "legacy_marker",
    "entry_headers",
    "annotate",
    "describe",
]
