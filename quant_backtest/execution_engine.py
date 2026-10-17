# -*- coding: utf-8 -*-
"""Deterministic, read-only minute execution over canonical :class:`BarStore` data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import math
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from bar_dto import Bar, BarQuery, ensure_utc
from bar_store import BarStore

Provider = Callable[..., Any]
SCHEMA = "minute-exec-v1"
ELIGIBILITY_POLICY_REF = "strict_next_bar_v1"
SAME_BAR_POLICY_REF = "same_bar_inclusive_v1"


def _calendar_bar_gate(provider: Any, bar: Bar) -> None:
    """Require explicit open-session coverage for one bar."""
    try:
        if not provider.is_open(bar.session_date):
            raise ValueError("CALENDAR_UNAVAILABLE: CLOSED_SESSION")
        provider.session_bounds(bar.session_date)
    except ValueError as exc:
        text = str(exc)
        if text.startswith("CALENDAR_"):
            raise
        raise ValueError(f"CALENDAR_UNAVAILABLE: {text}") from exc
    except Exception as exc:
        raise ValueError(f"CALENDAR_UNAVAILABLE: {exc}") from exc


def _bar_visible_at(bar: Bar, execution_time: datetime) -> bool:
    """Return whether a bar is closed and fully observable by execution time.

    ``available_at`` and ``close_at`` are optional source evidence.  When
    present, both timestamps must not be later than the time at which the
    order could execute; otherwise using the bar would introduce look-ahead.
    """
    if not bar.closed:
        return False
    if bar.available_at is not None and bar.available_at > execution_time:
        return False
    if bar.close_at is not None and bar.close_at > execution_time:
        return False
    return True


def _json_value(value: Any) -> Any:
    """Convert metadata values to deterministic JSON-compatible values."""
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution policy for one market/timeframe stream."""

    initial_cash: float = 0.0
    initial_positions: Mapping[str, float] = field(default_factory=dict)
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    lot_size: int = 1
    next_bar: bool = True
    t_plus_one: bool = True
    policy_ref: Optional[str] = None
    gap_stop_pct: Optional[float] = None
    market: Optional[str] = None
    timeframe: Optional[str] = None
    calendar_ref: Optional[str] = None
    calendar_version: Optional[str] = None
    calendar_required: bool = False
    # Kept only as a source-compatible alias; normalized to fee_bps below.
    fee_rate: Optional[float] = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.initial_cash)) or self.initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        fee_bps = float(self.fee_bps)
        if self.fee_rate is not None:
            if fee_bps and not math.isclose(fee_bps, float(self.fee_rate) * 10000.0):
                raise ValueError("fee_bps and fee_rate disagree")
            fee_bps = float(self.fee_rate) * 10000.0
            object.__setattr__(self, "fee_bps", fee_bps)
        if fee_bps < 0 or not math.isfinite(fee_bps):
            raise ValueError("fee_bps must be finite and non-negative")
        if self.slippage_bps < 0 or not math.isfinite(float(self.slippage_bps)):
            raise ValueError("slippage_bps must be finite and non-negative")
        if type(self.t_plus_one) is not bool:
            raise ValueError("t_plus_one must be boolean")
        if type(self.calendar_required) is not bool:
            raise ValueError("calendar_required must be boolean")
        if (self.calendar_ref is None) != (self.calendar_version is None):
            raise ValueError("CALENDAR_MISMATCH: calendar_ref and calendar_version must be paired")
        if self.policy_ref:
            from market_policy import get_policy
            policy = get_policy(self.market) if self.market else None
            if self.policy_ref not in {"registry:market-cn", "registry:market-us", "registry:market-hk", "registry:market-crypto"}:
                raise ValueError("POLICY_MISMATCH")
            if policy is not None and (self.policy_ref != policy.policy_ref or self.t_plus_one != policy.t_plus_one):
                raise ValueError("POLICY_MISMATCH")
        if int(self.lot_size) <= 0:
            raise ValueError("lot_size must be positive")
        if self.gap_stop_pct is not None and (
            self.gap_stop_pct < 0 or not math.isfinite(float(self.gap_stop_pct))
        ):
            raise ValueError("gap_stop_pct must be finite and non-negative")
        for symbol, quantity in self.initial_positions.items():
            if not str(symbol) or float(quantity) < 0 or not math.isfinite(float(quantity)):
                raise ValueError("initial_positions must contain non-negative finite quantities")


@dataclass(frozen=True)
class OrderIntent:
    """A strategy request submitted after an observed signal bar."""

    symbol: str
    side: str
    quantity: float
    submitted_at: datetime
    order_id: str = ""
    limit_price: Optional[float] = None
    market: Optional[str] = None
    timeframe: Optional[str] = None
    signal_at: Optional[datetime] = None
    order_type: str = "MARKET"
    stop_price: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        side = str(self.side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        if float(self.quantity) <= 0 or not math.isfinite(float(self.quantity)):
            raise ValueError("quantity must be finite and positive")
        submitted = ensure_utc(self.submitted_at, "submitted_at")
        signal_at = ensure_utc(self.signal_at or submitted, "signal_at")
        if submitted < signal_at:
            raise ValueError("submitted_at must not precede signal_at")
        object.__setattr__(self, "submitted_at", submitted)
        object.__setattr__(self, "signal_at", signal_at)
        order_type = str(self.order_type).upper()
        if order_type not in {"MARKET", "STOP"}:
            raise ValueError("order_type must be MARKET or STOP")
        object.__setattr__(self, "order_type", order_type)
        for name in ("limit_price", "stop_price"):
            value = getattr(self, name)
            if value is not None and (float(value) <= 0 or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    gross_value: float
    fee: float
    slippage: float
    dt: datetime
    source: str


@dataclass(frozen=True)
class OrderAudit:
    order_id: str
    symbol: str
    side: str
    requested_quantity: float
    status: str
    reason: str
    submitted_at: datetime
    eligible_at: Optional[datetime]
    fill_at: Optional[datetime]
    source_trace: Tuple[str, ...] = ()
    query_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def reason_code(self) -> str:
        """Canonical uppercase reason code for downstream audit consumers."""
        return self.reason

    @property
    def eligibility_policy_ref(self) -> Optional[str]:
        """Return the immutable eligibility policy used for this audit."""
        value = self.query_metadata.get("eligibility_policy_ref")
        return str(value) if value is not None else None


@dataclass(frozen=True)
class ExecutionResult:
    fills: Tuple[Fill, ...]
    audits: Tuple[OrderAudit, ...]
    cash: float
    positions: Mapping[str, float]
    equity: float
    source_metadata: Mapping[str, Any]
    query_metadata: Mapping[str, Any]
    orders: Tuple[OrderIntent, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    @property
    def unfilled(self) -> Tuple[OrderAudit, ...]:
        return tuple(item for item in self.audits if item.status != "filled")

    @property
    def unfilled_rate(self) -> float:
        return len(self.unfilled) / len(self.orders) if self.orders else 0.0


def _provider_call(provider: Optional[Provider], symbol: str, dt: datetime, bar: Bar) -> Any:
    if provider is None:
        return None
    try:
        return provider(symbol, dt, bar)
    except TypeError:
        return provider(symbol, dt)


def _status_reason(value: Any) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.upper()
        if normalized in {"TRADING", "OPEN", "OK", "ACTIVE"}:
            return None
        if normalized in {"HALTED", "SUSPENDED", "STOP", "CLOSED"}:
            return "HALTED"
        return "STATUS_UNKNOWN"
    if isinstance(value, Mapping):
        if value.get("halted") or value.get("suspended") or value.get("blocked"):
            return "HALTED"
        if value.get("status") is not None:
            return _status_reason(value["status"])
        return "STATUS_UNKNOWN"
    if value is True:
        return None
    if value is False:
        return "HALTED"
    return "STATUS_UNKNOWN"


def _limit_reason(value: Any, side: str, price: float) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.upper()
        return None if normalized in {"OPEN", "TRADING", "OK", "NONE"} else normalized if normalized in {"LIMIT_UP", "LIMIT_DOWN"} else "LIMIT_UNKNOWN"
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
        return "LIMIT_UP" if side == "BUY" and price >= float(value) else "LIMIT_DOWN" if side == "SELL" and price <= float(value) else None
    return "LIMIT_UNKNOWN"


def _quantity(value: float, lot_size: int) -> float:
    return math.floor(float(value) / lot_size) * lot_size


def run_from_store(
    store: BarStore,
    intents: Iterable[OrderIntent],
    config: Optional[ExecutionConfig] = None,
    *,
    query: Optional[BarQuery] = None,
    limit_provider: Optional[Provider] = None,
    status_provider: Optional[Provider] = None,
    calendar_provider: Optional[Any] = None,
) -> ExecutionResult:
    """Execute sorted intents against canonical bars, with no network or writes."""
    if not isinstance(store, BarStore):
        raise TypeError("store must be a BarStore")
    policy = config or ExecutionConfig()
    if not policy.policy_ref:
        raise ValueError("POLICY_MISMATCH: policy_ref required")
    calendar_identity = None
    if policy.calendar_required:
        try:
            from calendar_provider import require_production_provider, validate_identity
            identity = require_production_provider(calendar_provider)
            if policy.calendar_ref is None or policy.calendar_version is None:
                raise ValueError("CALENDAR_MISMATCH: calendar identity required")
            if identity.calendar_ref != policy.calendar_ref or identity.version != policy.calendar_version:
                raise ValueError("CALENDAR_MISMATCH: calendar identity differs")
            if policy.market and identity.market.upper() != policy.market.upper():
                raise ValueError("CALENDAR_MISMATCH: calendar market differs")
            store.validate_calendar_identity(policy.calendar_ref, policy.calendar_version)
            from market_policy import get_policy
            market_policy = get_policy(policy.market or identity.market)
            if identity.timezone != market_policy.timezone:
                raise ValueError("CALENDAR_MISMATCH: calendar timezone differs")
            calendar_identity = {"calendar_ref": identity.calendar_ref, "calendar_version": identity.version,
                                 "market": identity.market, "timezone": identity.timezone}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"CALENDAR_UNAVAILABLE: {exc}") from exc
    selected_query = query or BarQuery()
    bars = store.query(selected_query)
    ordered_intents = tuple(sorted(tuple(intents), key=lambda item: (item.signal_at, item.submitted_at, item.order_id)))
    execution_bars = tuple(
        bar for bar in bars
        if (not policy.market or bar.market.upper() == policy.market.upper())
        and (not policy.timeframe or bar.timeframe.lower() == policy.timeframe.lower())
        and any(
            bar.symbol.upper() == intent.symbol.upper()
            and (not intent.market or bar.market.upper() == intent.market.upper())
            and (not intent.timeframe or bar.timeframe.lower() == intent.timeframe.lower())
            for intent in ordered_intents
        )
    )
    for bar in execution_bars:
        if bar.adjustment != "none":
            raise ValueError(f"adjustment must be none: {bar.symbol} {bar.dt.isoformat()}")
    bars = execution_bars
    eligibility_policy_ref = ELIGIBILITY_POLICY_REF if policy.next_bar else SAME_BAR_POLICY_REF
    query_metadata = _json_value({"start": selected_query.start, "end": selected_query.end,
                                  "as_of": selected_query.as_of, "closed_only": selected_query.closed_only,
                                  "rows": len(bars), "market": policy.market, "timeframe": policy.timeframe,
                                  "eligibility_policy_ref": eligibility_policy_ref,
                                  "calendar": calendar_identity})
    by_key: Dict[Tuple[str, str, str], Tuple[Bar, ...]] = {}
    for bar in bars:
        if policy.market and bar.market.upper() != policy.market.upper():
            continue
        if policy.timeframe and bar.timeframe.lower() != policy.timeframe.lower():
            continue
        key = (bar.symbol.upper(), bar.market.upper(), bar.timeframe.lower())
        by_key[key] = by_key.get(key, tuple()) + (bar,)
    positions: Dict[str, float] = {str(k): float(v) for k, v in policy.initial_positions.items()}
    bought_lots: Dict[Tuple[str, date], float] = {}
    cash = float(policy.initial_cash)
    fills = []
    audits = []
    for index, intent in enumerate(ordered_intents):
        key_market = (intent.market or policy.market or "").upper()
        key_timeframe = (intent.timeframe or policy.timeframe or "").lower()
        candidates = [bar for key, values in by_key.items() if key[0] == intent.symbol.upper()
                      and (not key_market or key[1] == key_market)
                      and (not key_timeframe or key[2] == key_timeframe) for bar in values]
        candidates.sort(key=lambda item: item.dt)
        signal = next((bar for bar in candidates if bar.dt == intent.signal_at), None)
        if policy.calendar_required and signal is not None:
            _calendar_bar_gate(calendar_provider, signal)
        order_id = intent.order_id or f"order-{index + 1}"
        common = dict(order_id=order_id, symbol=intent.symbol, side=intent.side,
                      requested_quantity=float(intent.quantity), submitted_at=intent.submitted_at,
                      source_trace=tuple(signal.source_trace if signal else ()), query_metadata=query_metadata)
        if signal is None:
            audits.append(OrderAudit(status="unfilled", reason="SIGNAL_BAR_MISSING", eligible_at=None, fill_at=None, **common)); continue
        if not _bar_visible_at(signal, intent.submitted_at):
            audits.append(OrderAudit(status="unfilled", reason="SIGNAL_BAR_NOT_CLOSED", eligible_at=signal.dt, fill_at=None, **common)); continue
        if policy.next_bar:
            eligible = next((bar for bar in candidates
                             if bar.dt > intent.signal_at
                             and bar.dt >= intent.submitted_at
                             and _bar_visible_at(bar, bar.dt)
                             and (not policy.calendar_required or _calendar_bar_gate(calendar_provider, bar) is None)), None)
        else:
            # Same-bar execution is an explicit, strict policy: the signal
            # bar timestamp must equal the submission timestamp.  A later
            # submission within that bar cannot use already-observed OHLC.
            eligible = signal if signal.dt == intent.submitted_at and _bar_visible_at(signal, intent.submitted_at) and (not policy.calendar_required or _calendar_bar_gate(calendar_provider, signal) is None) else None
        if eligible is None:
            audits.append(OrderAudit(status="unfilled", reason="NO_NEXT_BAR", eligible_at=None, fill_at=None, **common)); continue
        if eligible.o is None or not math.isfinite(float(eligible.o)) or float(eligible.o) <= 0:
            audits.append(OrderAudit(status="unfilled", reason="DATA_GAP", eligible_at=eligible.dt, fill_at=None, **common)); continue
        status_reason = _status_reason(
            _provider_call(status_provider, intent.symbol, eligible.dt, eligible)
            if status_provider is not None else None
        )
        if status_reason:
            audits.append(OrderAudit(status="unfilled", reason=status_reason, eligible_at=eligible.dt, fill_at=None, **common)); continue
        raw_price = float(eligible.o)
        if intent.order_type == "STOP":
            if intent.side != "SELL" or intent.stop_price is None:
                audits.append(OrderAudit(status="unfilled", reason="INVALID_STOP", eligible_at=eligible.dt, fill_at=None, **common)); continue
            if eligible.o > intent.stop_price and (eligible.l is None or eligible.l > intent.stop_price):
                audits.append(OrderAudit(status="unfilled", reason="STOP_NOT_TRIGGERED", eligible_at=eligible.dt, fill_at=None, **common)); continue
            raw_price = float(eligible.o) if eligible.o <= intent.stop_price else float(intent.stop_price)
        if limit_provider is not None:
            limit_reason = _limit_reason(_provider_call(limit_provider, intent.symbol, eligible.dt, eligible), intent.side, raw_price)
            if limit_reason:
                audits.append(OrderAudit(status="unfilled", reason=limit_reason, eligible_at=eligible.dt, fill_at=None, **common)); continue
        quantity = _quantity(intent.quantity, policy.lot_size)
        if quantity <= 0:
            audits.append(OrderAudit(status="unfilled", reason="BELOW_LOT", eligible_at=eligible.dt, fill_at=None, **common)); continue
        if intent.side == "SELL" and policy.t_plus_one:
            same_or_later_buys = sum(qty for (symbol, session), qty in bought_lots.items()
                                     if symbol == intent.symbol and session >= eligible.session_date)
            if quantity > positions.get(intent.symbol, 0.0) - same_or_later_buys + 1e-9:
                audits.append(OrderAudit(status="unfilled", reason="T_PLUS_ONE", eligible_at=eligible.dt, fill_at=None, **common)); continue
        if intent.side == "SELL" and quantity > positions.get(intent.symbol, 0.0) + 1e-9:
            audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_POSITION", eligible_at=eligible.dt, fill_at=None, **common)); continue
        direction = 1.0 if intent.side == "BUY" else -1.0
        price = raw_price * (1.0 + direction * float(policy.slippage_bps) / 10000.0)
        gross = price * quantity
        fee = gross * float(policy.fee_bps) / 10000.0
        total = gross + fee if intent.side == "BUY" else gross - fee
        if intent.side == "BUY" and total > cash + 1e-9:
            audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_CASH", eligible_at=eligible.dt, fill_at=None, **common)); continue
        cash += -total if intent.side == "BUY" else total
        positions[intent.symbol] = positions.get(intent.symbol, 0.0) + (quantity if intent.side == "BUY" else -quantity)
        if intent.side == "BUY":
            bought_lots[(intent.symbol, eligible.session_date)] = bought_lots.get((intent.symbol, eligible.session_date), 0.0) + quantity
        fills.append(Fill(order_id, intent.symbol, intent.side, quantity, price, gross, fee,
                          abs(price - raw_price) * quantity, eligible.dt, eligible.source))
        audits.append(OrderAudit(status="filled", reason="FILLED", eligible_at=eligible.dt, fill_at=eligible.dt, **common))
    mark: Dict[Tuple[str, str, str], float] = {}
    for bar in bars:
        if policy.market and bar.market.upper() != policy.market.upper():
            continue
        if policy.timeframe and bar.timeframe.lower() != policy.timeframe.lower():
            continue
        mark[(bar.symbol.upper(), bar.market.upper(), bar.timeframe.lower())] = float(
            bar.c if bar.c is not None else bar.o or 0.0
        )
    equity = cash
    for symbol, quantity in positions.items():
        matching = [price for key, price in mark.items() if key[0] == symbol.upper()]
        equity += quantity * (matching[-1] if matching else 0.0)
    metrics = {"submitted": len(ordered_intents), "filled": len(fills),
               "unfilled": len(ordered_intents) - len(fills),
               "unfilled_rate": (len(ordered_intents) - len(fills)) / len(ordered_intents) if ordered_intents else 0.0}
    return ExecutionResult(tuple(fills), tuple(audits), cash, dict(positions), equity,
                           _json_value({**store.explain(), **({"calendar": calendar_identity} if calendar_identity else {})}), query_metadata, ordered_intents, metrics, SCHEMA)
