# -*- coding: utf-8 -*-
"""统一、可审计的市场交易规则判定。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import inspect
from typing import Any, Callable, Mapping, Optional, Tuple


RULE_REF = "market_rule_v1"
RULE_VERSION = "1"
Provider = Callable[..., Any]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    return value


@dataclass(frozen=True)
class RuleDecision:
    """One immutable market-rule decision and its provenance."""

    allowed: bool
    reason: Optional[str] = None
    rule_ref: str = RULE_REF
    rule_version: str = RULE_VERSION
    snapshot: Mapping[str, Any] = field(default_factory=dict)
    source_trace: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_ref or not self.rule_version:
            raise ValueError("rule_ref and rule_version are required")
        reason = self.reason
        if reason is None:
            reason = "ALLOWED" if self.allowed else "BLOCKED"
        object.__setattr__(self, "reason", str(reason).upper())
        object.__setattr__(self, "snapshot", _json_value(dict(self.snapshot)))
        object.__setattr__(self, "source_trace", tuple(str(x) for x in self.source_trace))

    @property
    def reason_code(self) -> str:
        return self.reason


class MarketRuleProvider:
    """Evaluate status/listing and price-limit rules without permissive fallbacks.

    Providers are deliberately injected.  Missing status/limit evidence produces a
    blocking decision (``STATUS_UNKNOWN``/``LIMIT_UNKNOWN``), except markets whose
    registered policy explicitly has no price limits.
    """

    def __init__(self, *, status_provider: Optional[Provider] = None,
                 limit_provider: Optional[Provider] = None,
                 listing_provider: Optional[Provider] = None,
                 market: Optional[str] = None,
                 rule_ref: str = RULE_REF, rule_version: str = RULE_VERSION,
                 source_trace: Tuple[str, ...] = ()):
        self.status_provider = status_provider
        self.limit_provider = limit_provider
        self.listing_provider = listing_provider
        self.market = str(market or "").upper()
        self.rule_ref = rule_ref
        self.rule_version = rule_version
        self.source_trace = tuple(str(x) for x in source_trace)

    @staticmethod
    def _call(provider: Provider, symbol: str, dt: Any, bar: Any) -> Any:
        """Call either the canonical 3-arg or legacy 2-arg provider signature."""
        try:
            signature = inspect.signature(provider)
        except (TypeError, ValueError):
            return provider(symbol, dt, bar)
        positional = [p for p in signature.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        accepts_varargs = any(p.kind == p.VAR_POSITIONAL for p in signature.parameters.values())
        if accepts_varargs or len(positional) >= 3:
            return provider(symbol, dt, bar)
        return provider(symbol, dt)

    @staticmethod
    def _status_reason(value: Any) -> Optional[str]:
        if isinstance(value, Mapping):
            if value.get("delisted") is True:
                return "DELISTED"
            if value.get("listed") is False or value.get("unlisted") is True:
                return "UNLISTED"
            if value.get("halted") or value.get("suspended") or value.get("blocked"):
                return "HALTED"
            if value.get("listed") is True:
                return None
            if value.get("status") is not None:
                return MarketRuleProvider._status_reason(value["status"])
            return "STATUS_UNKNOWN"
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in {"TRADING", "OPEN", "OK", "ACTIVE", "LISTED"}:
                return None
            if normalized in {"HALTED", "SUSPENDED", "STOP", "CLOSED"}:
                return "HALTED"
            if normalized in {"UNLISTED", "NOT_LISTED", "PRELISTED"}:
                return "UNLISTED"
            if normalized in {"DELISTED", "退市"}:
                return "DELISTED"
            return "STATUS_UNKNOWN"
        if value is True:
            return None
        if value is False:
            return "HALTED"
        return "STATUS_UNKNOWN"

    @staticmethod
    def _limit_reason(value: Any, side: str, price: float) -> Optional[str]:
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in {"OPEN", "TRADING", "OK", "NONE"}:
                return None
            if normalized in {"LIMIT_UP", "LIMIT_DOWN"}:
                return normalized
            return "LIMIT_UNKNOWN"
        if isinstance(value, Mapping):
            upper = value.get("limit_up", value.get("upper"))
            lower = value.get("limit_down", value.get("lower"))
            if side == "BUY" and upper is not None and price >= float(upper):
                return "LIMIT_UP"
            if side == "SELL" and lower is not None and price <= float(lower):
                return "LIMIT_DOWN"
            if value.get("known") is False:
                return "LIMIT_UNKNOWN"
            return None
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            lower, upper = value[0], value[1]
            if side == "BUY" and upper is not None and price >= float(upper):
                return "LIMIT_UP"
            if side == "SELL" and lower is not None and price <= float(lower):
                return "LIMIT_DOWN"
            return None
        if isinstance(value, (int, float)):
            return ("LIMIT_UP" if side == "BUY" and price >= float(value)
                    else "LIMIT_DOWN" if side == "SELL" and price <= float(value) else None)
        return "LIMIT_UNKNOWN"

    def _decision(self, allowed: bool, reason: str, snapshot: Mapping[str, Any]) -> RuleDecision:
        return RuleDecision(allowed, reason, self.rule_ref, self.rule_version, snapshot, self.source_trace)

    def _evaluate(self, symbol: str, dt: Any, bar: Any, side: str, price: float) -> RuleDecision:
        status = self._call(self.status_provider, symbol, dt, bar) if self.status_provider else None
        snapshot = {"status": status}
        if self.listing_provider is not None:
            listing = self._call(self.listing_provider, symbol, dt, bar)
            snapshot["listing"] = listing
            listing_reason = self._status_reason(listing)
            if listing_reason:
                return self._decision(False, listing_reason, snapshot)
        status_reason = self._status_reason(status)
        if status_reason:
            return self._decision(False, status_reason, snapshot)
        has_limits = False
        try:
            from market_policy import get_policy
            market = self.market or str(getattr(bar, "market", ""))
            has_limits = get_policy(market).provider_capability == "limit_up_down"
        except Exception:
            has_limits = True
        if self.limit_provider is None:
            if has_limits:
                return self._decision(False, "LIMIT_UNKNOWN", snapshot)
            return self._decision(True, "ALLOWED", snapshot)
        limits = self._call(self.limit_provider, symbol, dt, bar)
        snapshot["limit"] = limits
        limit_reason = self._limit_reason(limits, side, float(price))
        if limit_reason:
            return self._decision(False, limit_reason, snapshot)
        return self._decision(True, "ALLOWED", snapshot)

    def decide(self, symbol: str, dt: Any, bar: Any, side: str, price: float) -> RuleDecision:
        return self._evaluate(symbol, dt, bar, side, price)

    def evaluate(self, *args: Any, **kwargs: Any) -> RuleDecision:
        """Evaluate canonical args or ``evaluate(order, bar)`` adapter form."""
        if len(args) == 2 and not kwargs:
            order, bar = args
            symbol = getattr(order, "symbol", None) or order.get("symbol")
            side = getattr(order, "side", None) or order.get("side")
            price = getattr(order, "limit_price", None) or getattr(bar, "o", None)
            dt = getattr(bar, "dt", None)
            if isinstance(order, Mapping):
                price = order.get("limit_price") or price
            return self._evaluate(symbol, dt, bar, side, float(price))
        return self._evaluate(*args, **kwargs)


__all__ = ["MarketRuleProvider", "RuleDecision", "RULE_REF", "RULE_VERSION"]
