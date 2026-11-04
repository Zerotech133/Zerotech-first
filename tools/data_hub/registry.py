# -*- coding: utf-8 -*-
"""源登记表的加载与校验。校验只报问题、不抛异常，便于 CI 与治理报告复用。"""

from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SOURCES_FILE = HERE / "sources.yaml"
REPO = HERE.parent.parent
CATALOG_FILE = REPO / "quant_backtest" / "data" / "catalog.json"
WAREHOUSE_REGISTRY = REPO / "quant_backtest" / "data" / "warehouse_registry.json"

DOMAINS = {
    "quote",
    "finance",
    "disclosure",
    "flow",
    "macro",
    "derivatives",
    "overseas",
    "crypto",
    "alt",
}
LICENSES = {"public_api", "public_page", "open_license", "paid", "unknown"}
STATUSES = {"verified", "reachable_guarded", "unreachable", "planned"}
FREQS = {"RT", "D", "W", "M", "Q", "A", "EVENT"}
POLICIES = {"direct", "env", "local", "auto"}
ADAPTERS = {"tushare", "http_json", "http_csv", "http_html", "file", "sdk"}

REQUIRED = (
    "id",
    "name",
    "domain",
    "home",
    "license",
    "freq",
    "proxy_policy",
    "status",
    "adapter",
    "covers",
    "linked_tables",
)


def load(path: Path | None = None) -> dict:
    """返回 {'meta': {...}, 'sources': [...]}；文件缺失或格式错直接抛，属启动期错误。"""
    p = Path(path or SOURCES_FILE)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"源登记表结构非法: {p}")
    return data


def validate(data: dict) -> list[str]:
    """结构性校验。返回问题列表，空列表代表合格。"""
    problems: list[str] = []
    seen: set[str] = set()
    for idx, src in enumerate(data.get("sources", [])):
        if not isinstance(src, dict):
            problems.append(f"#{idx} 不是映射")
            continue
        sid = src.get("id") or f"#{idx}"
        missing = [k for k in REQUIRED if k not in src]
        if missing:
            problems.append(f"{sid}: 缺字段 {missing}")
        if sid in seen:
            problems.append(f"{sid}: id 重复")
        seen.add(sid)
        checks = (
            ("domain", DOMAINS),
            ("license", LICENSES),
            ("status", STATUSES),
            ("freq", FREQS),
            ("proxy_policy", POLICIES),
            ("adapter", ADAPTERS),
        )
        for field, allowed in checks:
            val = src.get(field)
            if val is not None and val not in allowed:
                problems.append(f"{sid}: {field}={val!r} 不在 {sorted(allowed)}")
        if not isinstance(src.get("covers", []), list):
            problems.append(f"{sid}: covers 必须是列表")
        if not isinstance(src.get("linked_tables", []), list):
            problems.append(f"{sid}: linked_tables 必须是列表")
        probe = src.get("probe")
        if probe is not None and not isinstance(probe, dict):
            problems.append(f"{sid}: probe 必须是映射")
    return problems


def by_id(data: dict) -> dict[str, dict]:
    return {s["id"]: s for s in data.get("sources", []) if isinstance(s, dict) and s.get("id")}


def summary(data: dict) -> dict:
    """按域与状态计数，供报告头使用。"""
    dom: dict[str, int] = {}
    sta: dict[str, int] = {}
    for s in data.get("sources", []):
        dom[s.get("domain", "?")] = dom.get(s.get("domain", "?"), 0) + 1
        sta[s.get("status", "?")] = sta.get(s.get("status", "?"), 0) + 1
    return {"total": len(data.get("sources", [])), "by_domain": dom, "by_status": sta}


def table_prefixes(src: dict) -> tuple[str, ...]:
    return tuple(str(t) for t in (src.get("linked_tables") or []) if str(t).strip())


def match_tables(src: dict, table_names) -> list[str]:
    """按 linked_tables 的前缀匹配 catalog 表名。空前缀视为不声明（不匹配任何表）。"""
    prefixes = table_prefixes(src)
    if not prefixes:
        return []
    return sorted(n for n in table_names if any(n.startswith(p) for p in prefixes))
