# -*- coding: utf-8 -*-
"""各源响应的归一化解析（兼容雪球嵌套 data 串与扁平两种结构）。"""

from __future__ import annotations

import json
from datetime import datetime

from ..core import ms_to_dt


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _unwrap(item: dict) -> dict:
    """外层 feed 项可能是 {id, data:"{...}"} 嵌套，也可能已是扁平结构。

    - 有 data 且为 JSON 串 → 解析内层 dict；
    - 有 data 且已是 dict → 直接取；
    - 无 data 包裹（扁平结构）→ item 自身即内容（兼容 listV2 等端点）。
    """
    if not isinstance(item, dict):
        return {}
    d = item.get("data")
    if d is None:
        return item
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            return {}
    return d if isinstance(d, dict) else {}


def parse_status(item: dict, category) -> dict | None:
    """公开帖 / 热门帖 / 用户帖 通用归一化（status_id 为主键）。"""
    d = _unwrap(item)
    if not isinstance(d, dict) or not d.get("id"):
        return None
    row = {
        "status_id": str(d["id"]),
        "created_at": ms_to_dt(d.get("created_at")),
        "title": str(d.get("title") or "")[:120],
        "desc": str(d.get("description") or d.get("text") or "")[:2000],
        "user_id": str(d.get("user_id") or (d.get("user") or {}).get("id") or ""),
        "target": str(d.get("target") or ""),
        "source": str(d.get("source") or d.get("client") or "")[:60],
        "category": int(category) if str(category).lstrip("-").isdigit() else -1,
        "fetched_at": _now(),
    }
    row["comment_count"] = d.get("comment_count") or d.get("reply_count") or 0
    for f in ("view_count", "retweet_count", "like_count", "reward_count"):
        row[f] = d.get(f) or 0
    return row


def parse_hot(item: dict, category) -> dict | None:
    """热门帖：在 status 基础上补 hot_rank / hot_fallback。"""
    r = parse_status(item, category)
    if r is None:
        return None
    d = _unwrap(item)
    r["hot_rank"] = d.get("hot_rank")
    r["hot_fallback"] = d.get("hot_fallback")
    return r


def parse_quote(symbol: str, payload: dict) -> dict | None:
    """pysnowball.quotec 返回 {data:{items:[ {...} ]}} → 单只快照行。"""
    if not isinstance(payload, dict):
        return None
    items = ((payload.get("data") or {}).get("items")) if isinstance(payload.get("data"), dict) else None
    if not items:
        return None
    it = items[0] if isinstance(items, list) else None
    if not isinstance(it, dict):
        return None
    return {
        "symbol": str(it.get("symbol") or symbol),
        "ts": _now(),
        "open": it.get("open"),
        "high": it.get("high"),
        "low": it.get("low"),
        "close": it.get("current") if it.get("current") is not None else it.get("last_close"),
        "volume": it.get("volume"),
        "amount": it.get("amount"),
        "pct_chg": it.get("pct_chg"),
        "fetched_at": _now(),
    }


def parse_kline(symbol: str, payload: dict) -> list[dict]:
    """pysnowball.kline 返回 {data:{column:[...], item:[[...]]}} → 多日 K 线行。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    cols = data.get("column") or data.get("columns") or []
    rows_raw = data.get("item") or data.get("items") or []
    if not cols or not rows_raw:
        return []
    idx = {name: i for i, name in enumerate(cols)}
    out = []
    for r in rows_raw:
        if not isinstance(r, (list, tuple)) or len(r) <= max(idx.values()):
            continue
        date = r[idx.get("date", idx.get("timestamp", 0))]
        out.append({
            "symbol": str(symbol),
            "date": str(date)[:10],
            "open": _num(r, idx.get("open")),
            "high": _num(r, idx.get("high")),
            "low": _num(r, idx.get("low")),
            "close": _num(r, idx.get("close")),
            "volume": _num(r, idx.get("volume")),
            "amount": _num(r, idx.get("amount")),
        })
    return out


def _num(r, i) -> float | None:
    if i is None or i >= len(r):
        return None
    v = r[i]
    try:
        return float(v) if v is not None else None
    except Exception:
        return None
