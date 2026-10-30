# -*- coding: utf-8 -*-
"""资产生命周期覆盖读取（catalog/registry/freshness 共用，fail-open）。

真源：`agent_platform/catalogs/data_lifecycle.yaml`（手工维护，2026-10-28 建立）。
- planned / deprecated / archived 的资产不计入资格欠账（registry）与新鲜度告警（freshness）；
- 读失败一律返回空覆盖（不阻断任何主流程）。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OVERRIDES = REPO / "agent_platform" / "catalogs" / "data_lifecycle.yaml"
VALID_STATUSES = ("active", "planned", "deprecated", "archived")
SKIP_STATUSES = ("planned", "deprecated", "archived")


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        import yaml  # noqa: WPS433
    except ImportError:
        return {"tables": {}}
    try:
        data = yaml.safe_load(OVERRIDES.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {"tables": {}}
    tables = data.get("tables") or {}
    return {"tables": tables if isinstance(tables, dict) else {}}


def status_for(table: str) -> str:
    """返回 planned/deprecated/archived/active（未知值归一 active）。"""
    rec = (_load()["tables"].get(table) or {})
    status = str(rec.get("lifecycle") or "active").strip().lower()
    return status if status in VALID_STATUSES else "active"


def reason_for(table: str) -> str:
    return str((_load()["tables"].get(table) or {}).get("reason") or "")


def skipped(table: str) -> bool:
    """planned/deprecated/archived → True（资格/新鲜度检查应跳过）。"""
    return status_for(table) in SKIP_STATUSES


def all_overrides() -> dict:
    return dict(_load()["tables"])
