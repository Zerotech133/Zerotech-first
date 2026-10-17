# -*- coding: utf-8 -*-
"""Canonical market policies shared by providers, aggregation, and execution."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def timezone_for(name: str):
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("POLICY_UNAVAILABLE: TIMEZONE_DATA_MISSING") from exc

@dataclass(frozen=True)
class MarketPolicy:
    market: str
    timezone: str
    t_plus_one: bool
    policy_ref: str
    sessions: tuple[str, ...]
    provider_capability: str
    lot_size: int = 1
    settlement: str = "T+0"

POLICIES = {
    "CN": MarketPolicy("CN", "Asia/Shanghai", True, "registry:market-cn", ("09:30-11:30", "13:00-15:00"), "limit_up_down", 100, "T+1"),
    "US": MarketPolicy("US", "America/New_York", False, "registry:market-us", ("09:30-16:00",), "no_limit", 1, "T+0"),
    "HK": MarketPolicy("HK", "Asia/Hong_Kong", False, "registry:market-hk", ("09:30-12:00", "13:00-16:00"), "no_limit", 1, "T+0"),
    "CRYPTO": MarketPolicy("CRYPTO", "UTC", False, "registry:market-crypto", ("00:00-24:00",), "no_limit", 1, "T+0"),
}

def get_policy(market: str) -> MarketPolicy:
    key = str(market or "").upper()
    if key in {"SSE", "SZSE", "SH", "SZ"}:
        key = "CN"
    if key not in POLICIES:
        raise ValueError("unsupported market policy")
    return POLICIES[key]

def normalize_timezone(market: str, timezone_name: str) -> str:
    policy = get_policy(market)
    if str(timezone_name) != policy.timezone:
        raise ValueError("TIMEZONE_MISMATCH")
    return policy.timezone

def session_date_for(dt: datetime, market: str):
    policy = get_policy(market)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("trade_time must be timezone-aware")
    return dt.astimezone(timezone_for(policy.timezone)).date()
