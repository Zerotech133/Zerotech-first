# -*- coding: utf-8 -*-
"""Deterministic, read-only minute execution over canonical :class:`BarStore` data."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import math
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from bar_dto import Bar, BarQuery, ensure_utc
from bar_store import BarStore
from market_rules import MarketRuleProvider
from fill_model import AllOrNoneFillModel, FillDecision, FillModel, VolumeParticipationFillModel
from fee_schedule import CostBreakdown, CostCalculator, OrderFeeAccumulator

Provider = Callable[..., Any]
# Public compatibility imports for callers importing execution contracts from
# the historical execution_engine module.  No ``__all__`` is declared here so
# legacy wildcard imports retain their previous surface.
SCHEMA = "minute-exec-v1"
ELIGIBILITY_POLICY_REF = "strict_next_bar_v1"
SAME_BAR_POLICY_REF = "same_bar_inclusive_v1"


def _calendar_bar_gate(provider: Any, bar: Bar) -> None:
    """Require explicit open-session coverage and segment membership for a bar."""
    try:
        if not provider.is_open(bar.session_date):
            raise ValueError("CALENDAR_UNAVAILABLE: CLOSED_SESSION")
        bounds = provider.session_bounds(bar.session_date)
        if not provider.is_in_session(bar.dt, session_date=bar.session_date):
            raise ValueError("CALENDAR_UNAVAILABLE: OUT_OF_SESSION")
        segments = tuple(getattr(bounds, "segments", ()) or ())
        if not segments:
            segments = (bounds,)
        matching = tuple(segment for segment in segments if segment.open_at <= bar.dt < segment.close_at)
        if not matching:
            raise ValueError("CALENDAR_UNAVAILABLE: OUT_OF_SESSION")
        if bar.close_at is not None and bar.close_at > matching[0].close_at:
            raise ValueError("CALENDAR_UNAVAILABLE: BAR_CLOSE_AFTER_SESSION")
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


def _limit_metadata(
    intent: "OrderIntent",
    *,
    trigger: Optional[str] = None,
    raw_price: Optional[float] = None,
    model_price: Optional[float] = None,
    slipped_price: Optional[float] = None,
    final_price: Optional[float] = None,
    slippage_bps: Optional[float] = None,
    slippage_clamped: bool = False,
) -> Dict[str, Any]:
    """Build stable, JSON-safe metadata for every LIMIT audit outcome."""
    limit_price = float(intent.limit_price) if intent.limit_price is not None else None
    bound = "upper" if intent.side == "BUY" else "lower"
    return _json_value({
        "order_type": "LIMIT",
        "limit_price": limit_price,
        "limit_trigger": trigger,
        "raw_price": raw_price,
        "model_price": model_price,
        "slipped_price": slipped_price,
        "final_price": final_price,
        "slippage_bps": slippage_bps,
        "slippage_clamped": bool(slippage_clamped),
        "price_bound": bound,
    })


@dataclass(frozen=True)
class InventoryLot:
    """One auditable inventory lot with session-based availability."""

    symbol: str
    market: str
    exchange: str
    asset_class: str
    acquisition_session: Optional[date]
    available_session: Optional[date]
    quantity: float
    lot_id: str

    def __post_init__(self) -> None:
        if not str(self.symbol).strip() or not str(self.market).strip() or not str(self.exchange).strip():
            raise ValueError("InventoryLot identity fields are required")
        if not str(self.asset_class).strip() or not str(self.lot_id).strip():
            raise ValueError("InventoryLot asset_class and lot_id are required")
        for name in ("acquisition_session", "available_session"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
                raise ValueError(f"{name} must be a date or None")
        if self.acquisition_session is not None and self.available_session is not None and self.available_session < self.acquisition_session:
            raise ValueError("available_session must not precede acquisition_session")
        if float(self.quantity) <= 0 or not math.isfinite(float(self.quantity)):
            raise ValueError("InventoryLot quantity must be finite and positive")


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
    # New fields trail every historical positional argument.
    market_rules_required: bool = False
    # Kept only as a source-compatible alias; normalized to fee_bps below.
    fee_rate: Optional[float] = None
    initial_lots: Tuple[InventoryLot, ...] = field(default_factory=tuple)
    # Optional opt-in decision provider.  ``None`` preserves the historical
    # all-or-none execution path and every legacy positional argument.
    fill_model: Optional[FillModel] = None
    # Explicit fee DTO hooks.  A configured calculator/provider is authoritative
    # for fee calculation; legacy fee_bps remains available only when no schedule
    # hook is configured.
    fee_calculator: Optional[Any] = None
    fee_provider: Optional[Any] = None
    fee_mode: str = "PER_FILL"
    # Currency is appended to preserve all historical positional arguments.
    currency: str = "CNY"

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.initial_cash)) or self.initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        fee_bps = float(self.fee_bps)
        if self.fee_rate is False:
            object.__setattr__(self, "fee_rate", None)
        if self.fee_rate is not None:
            if fee_bps and not math.isclose(fee_bps, float(self.fee_rate) * 10000.0):
                raise ValueError("fee_bps and fee_rate disagree")
            fee_bps = float(self.fee_rate) * 10000.0
            object.__setattr__(self, "fee_bps", fee_bps)
        if fee_bps < 0 or not math.isfinite(fee_bps):
            raise ValueError("fee_bps must be finite and non-negative")
        mode = str(self.fee_mode).upper()
        if mode not in {"PER_FILL", "PER_ORDER"}:
            raise ValueError("FEE_MODE_INVALID")
        object.__setattr__(self, "fee_mode", mode)
        currency = str(self.currency).strip().upper()
        if not currency:
            raise ValueError("currency must be non-empty")
        object.__setattr__(self, "currency", currency)
        if self.fee_calculator is not None and self.fee_provider is not None:
            raise ValueError("FEE_CONFIG_CONFLICT")
        if (self.fee_calculator is not None or self.fee_provider is not None) and fee_bps:
            raise ValueError("FEE_CONFIG_CONFLICT")
        if self.slippage_bps < 0 or not math.isfinite(float(self.slippage_bps)):
            raise ValueError("slippage_bps must be finite and non-negative")
        if type(self.t_plus_one) is not bool:
            raise ValueError("t_plus_one must be boolean")
        if type(self.calendar_required) is not bool:
            raise ValueError("calendar_required must be boolean")
        if self.market_rules_required is None:
            object.__setattr__(self, "market_rules_required", False)
        if type(self.market_rules_required) is not bool:
            raise ValueError("market_rules_required must be boolean")
        # Legacy positional callers used the former trailing boolean slot;
        # normalize ``calendar_version=False`` to an absent identity.
        if self.calendar_ref is None and self.calendar_version is False:
            object.__setattr__(self, "calendar_version", None)
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
            self.gap_stop_pct < 0 or self.gap_stop_pct >= 1 or not math.isfinite(float(self.gap_stop_pct))
        ):
            raise ValueError("gap_stop_pct must be finite and in [0, 1)")
        for symbol, quantity in self.initial_positions.items():
            if not str(symbol) or float(quantity) < 0 or not math.isfinite(float(quantity)):
                raise ValueError("initial_positions must contain non-negative finite quantities")
        if self.initial_lots is None:
            object.__setattr__(self, "initial_lots", ())
        elif not isinstance(self.initial_lots, tuple) or any(not isinstance(lot, InventoryLot) for lot in self.initial_lots):
            raise ValueError("initial_lots must contain InventoryLot values")
        if self.initial_positions and self.initial_lots:
            raise ValueError("initial_positions and initial_lots are mutually exclusive")


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
    exchange: Optional[str] = None
    asset_class: str = "equity"
    # Appended order lifecycle controls. DAY remains the historical one-shot default;
    # only GTC may retry across later bars.
    time_in_force: str = "DAY"
    expire_at: Optional[datetime] = None
    max_attempts: Optional[int] = None

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
        if order_type not in {"MARKET", "STOP", "LIMIT"}:
            raise ValueError("order_type must be MARKET, LIMIT, or STOP")
        object.__setattr__(self, "order_type", order_type)
        time_in_force = str(self.time_in_force).upper().strip()
        if time_in_force not in {"DAY", "GTC"}:
            raise ValueError("time_in_force must be DAY or GTC")
        object.__setattr__(self, "time_in_force", time_in_force)
        if self.expire_at is not None:
            expire_at = ensure_utc(self.expire_at, "expire_at")
            if expire_at < submitted:
                raise ValueError("expire_at must not precede submitted_at")
            object.__setattr__(self, "expire_at", expire_at)
        if self.max_attempts is not None:
            if isinstance(self.max_attempts, bool) or int(self.max_attempts) <= 0:
                raise ValueError("max_attempts must be a positive integer")
            object.__setattr__(self, "max_attempts", int(self.max_attempts))
        if time_in_force == "DAY" and (self.max_attempts is not None or self.expire_at is not None):
            raise ValueError("INVALID_TIF_CONFIG")
        if order_type == "LIMIT":
            value = self.limit_price
            if value is None:
                raise ValueError("INVALID_LIMIT")
            try:
                valid_limit = math.isfinite(float(value)) and float(value) > 0
            except (TypeError, ValueError):
                valid_limit = False
            if not valid_limit:
                raise ValueError("INVALID_LIMIT")
        for name in ("limit_price", "stop_price"):
            value = getattr(self, name)
            if value is not None:
                try:
                    valid_value = math.isfinite(float(value)) and float(value) > 0
                except (TypeError, ValueError):
                    valid_value = False
                if not valid_value:
                    reason = "INVALID_LIMIT" if name == "limit_price" and order_type == "LIMIT" else f"{name} must be finite and positive"
                    raise ValueError(reason)


@dataclass
class OrderFillState:
    """Public mutable state snapshot for one DAY/GTC order lifecycle."""

    order_id: str
    requested_quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    fill_count: int = 0
    status: str = "NEW"
    time_in_force: str = "DAY"
    attempts: int = 0
    first_fill_at: Optional[datetime] = None
    last_fill_at: Optional[datetime] = None
    source_trace: Tuple[str, ...] = ()
    terminal_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not str(self.order_id).strip() or float(self.requested_quantity) < 0:
            raise ValueError("invalid order fill state")
        self.time_in_force = str(self.time_in_force).upper()
        if self.time_in_force not in {"DAY", "GTC"}:
            raise ValueError("time_in_force must be DAY or GTC")
        self.remaining_quantity = max(0.0, float(self.requested_quantity) - float(self.filled_quantity))

    def accept_fill(self, quantity: float, at: datetime, source_trace: Iterable[str] = ()) -> None:
        """Record one accepted fill and maintain lifecycle invariants."""
        amount = float(quantity)
        if amount <= 0 or amount > self.remaining_quantity + 1e-9:
            raise ValueError("fill quantity exceeds remaining order quantity")
        self.filled_quantity += amount
        self.remaining_quantity = max(0.0, float(self.requested_quantity) - self.filled_quantity)
        self.fill_count += 1
        self.first_fill_at = self.first_fill_at or at
        self.last_fill_at = at
        self.status = "FILLED" if self.remaining_quantity <= 1e-9 else "PARTIALLY_FILLED"
        self.source_trace = tuple(dict.fromkeys(tuple(self.source_trace) + tuple(str(item) for item in source_trace)))


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
    # Audit extensions trail all historical positional fields.
    fill_model_ref: Optional[str] = None
    fill_model_version: Optional[str] = None
    fill_reason: str = "FILLED"
    fill_source_trace: Tuple[str, ...] = ()
    # Fee metadata trails historical positional fields.
    fee_mode: str = "LEGACY_BPS"
    fee_source: str = "legacy_bps"
    fee_schedule_ref: Optional[str] = None
    fee_schedule_version: Optional[str] = None
    fee_schedule_identity: Optional[str] = None
    fee_schedule_hash: Optional[str] = None
    fee_breakdown: Mapping[str, Any] = field(default_factory=dict)


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
    # Audit extensions trail all historical positional fields.
    fill_model_ref: Optional[str] = None
    fill_model_version: Optional[str] = None
    fill_reason: Optional[str] = None
    fill_source_trace: Tuple[str, ...] = ()
    # Quantity accounting is appended to preserve historical positional fields.
    filled_quantity: float = 0.0
    remaining_quantity: Optional[float] = None
    fill_count: int = 0
    # Fee metadata trails historical positional fields.
    fee_mode: str = "LEGACY_BPS"
    fee_source: str = "legacy_bps"
    fee_schedule_ref: Optional[str] = None
    fee_schedule_version: Optional[str] = None
    fee_schedule_identity: Optional[str] = None
    fee_schedule_hash: Optional[str] = None
    fee_breakdown: Mapping[str, Any] = field(default_factory=dict)

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
    inventory_lots: Tuple[InventoryLot, ...] = ()
    instrument_positions: Mapping[Tuple[str, str, str, str], float] = field(default_factory=dict)

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


def _instrument_identity(symbol: str, market: str, exchange: Optional[str] = None,
                         asset_class: Optional[str] = None) -> Tuple[str, str, str, str]:
    normalized_symbol = str(symbol).strip().upper()
    normalized_market = str(market or "UNKNOWN").strip().upper()
    if exchange:
        normalized_exchange = str(exchange).strip().upper()
    elif normalized_market == "CN" and normalized_symbol.endswith(".SH"):
        normalized_exchange = "SSE"
    elif normalized_market == "CN" and normalized_symbol.endswith(".SZ"):
        normalized_exchange = "SZSE"
    else:
        normalized_exchange = "UNKNOWN"
    return normalized_market, normalized_exchange, str(asset_class or "equity").strip().lower(), normalized_symbol


def _identity_has_placeholder(identity: Tuple[str, str, str, str]) -> bool:
    """Reject synthetic or unknown market/instrument ownership labels."""
    return any(not str(part).strip() or str(part).strip().upper() == "UNKNOWN"
               for part in identity)


def _bar_instrument(bar: Bar) -> Optional[Tuple[Tuple[str, str, str, str], str]]:
    """Return evidenced instrument and quote currency; never guess other markets."""
    market = bar.market.strip().upper()
    symbol = bar.symbol.strip().upper()
    if market == "UNKNOWN" or not market or symbol == "UNKNOWN" or not symbol:
        return None
    if any(value is not None and (not str(value).strip() or str(value).strip().upper() == "UNKNOWN")
           for value in (bar.exchange, bar.asset_class, bar.quote_currency)):
        return None
    inferred_exchange = ("SSE" if symbol.endswith(".SH") else
                         "SZSE" if symbol.endswith(".SZ") else None) if market == "CN" else None
    if inferred_exchange and bar.exchange and bar.exchange != inferred_exchange:
        return None
    if inferred_exchange and bar.asset_class and bar.asset_class != "equity":
        return None
    if inferred_exchange and bar.quote_currency and bar.quote_currency != "CNY":
        return None
    exchange = bar.exchange or inferred_exchange
    asset_class = bar.asset_class or ("equity" if inferred_exchange else None)
    currency = bar.quote_currency or ("CNY" if inferred_exchange else None)
    if not exchange or not asset_class or not currency:
        return None
    if inferred_exchange is None and (bar.exchange is None or bar.asset_class is None
                                      or bar.quote_currency is None):
        return None
    identity = _instrument_identity(symbol, market, exchange, asset_class)
    if _identity_has_placeholder(identity):
        return None
    return identity, currency


def _initial_inventory_lots(policy: ExecutionConfig) -> Tuple[InventoryLot, ...]:
    """Project legacy symbol positions into the same lots used by one-shot execution."""
    default_market = (policy.market or (policy.policy_ref.rsplit("-", 1)[-1]
                                        if policy.policy_ref else "UNKNOWN")).upper()
    lots = list(policy.initial_lots)
    lot_counter = 0
    for symbol, quantity in policy.initial_positions.items():
        if float(quantity) > 0:
            lot_counter += 1
            market, exchange, asset_class, _ = _instrument_identity(str(symbol), default_market)
            lots.append(InventoryLot(str(symbol), market, exchange, asset_class, None, None,
                                     float(quantity), f"initial-{lot_counter}"))
    return tuple(lots)


def _value_inventory(
    store: BarStore, query: Optional[BarQuery], cash: float,
    lots: Tuple[InventoryLot, ...], currency: str = "CNY",
) -> Tuple[float, Dict[Tuple[str, str, str, str], float]]:
    """Mark authoritative lots using latest visible history at the query cutoff.

    The execution query's start bound does not erase earlier valid marks. Missing
    prices reject valuation rather than silently assigning a zero asset value.
    """
    instrument_positions: Dict[Tuple[str, str, str, str], float] = {}
    for lot in lots:
        identity = _instrument_identity(lot.symbol, lot.market, lot.exchange, lot.asset_class)
        if _identity_has_placeholder(identity):
            raise ValueError(f"VALUATION_PRICE_UNAVAILABLE: invalid lot identity {identity}")
        instrument_positions[identity] = instrument_positions.get(identity, 0.0) + float(lot.quantity)
    if not instrument_positions:
        return float(cash), instrument_positions
    boundaries = [bound for bound in (query.end, query.as_of) if bound is not None] if query else []
    all_bars = store.all()
    if not boundaries and all_bars:
        boundaries.append(max(bar.dt for bar in all_bars))
    if not boundaries:
        raise ValueError("VALUATION_PRICE_UNAVAILABLE: no terminal valuation time")
    cutoff = min(boundaries)
    mark: Dict[Tuple[str, str, str, str], Tuple[float, str]] = {}
    for bar in store.query(BarQuery(end=cutoff, as_of=cutoff, closed_only=True)):
        instrument = _bar_instrument(bar)
        if instrument is None:
            continue
        identity, quote_currency = instrument
        price = bar.c if bar.c is not None else bar.o
        try:
            valid_price = price is not None and math.isfinite(float(price)) and float(price) > 0
        except (TypeError, ValueError, OverflowError):
            valid_price = False
        if valid_price:
            if identity in mark and mark[identity][1] != quote_currency:
                raise ValueError(f"VALUATION_CURRENCY_CONFLICT: {identity}")
            mark[identity] = float(price), quote_currency
    equity = float(cash)
    for identity, quantity in instrument_positions.items():
        if identity not in mark:
            raise ValueError(f"VALUATION_PRICE_UNAVAILABLE: {identity} at {cutoff.isoformat()}")
        price, quote_currency = mark[identity]
        if quote_currency != currency.upper():
            raise ValueError(f"FX_RATE_UNAVAILABLE: {quote_currency}/{currency.upper()} {identity}")
        equity += quantity * price
    return equity, instrument_positions


def _run_from_store_once(
    store: BarStore,
    intents: Iterable[OrderIntent],
    config: Optional[ExecutionConfig] = None,
    *,
    query: Optional[BarQuery] = None,
    limit_provider: Optional[Provider] = None,
    status_provider: Optional[Provider] = None,
    calendar_provider: Optional[Any] = None,
    market_rule_provider: Optional[MarketRuleProvider] = None,
    _execution_signal_at: Optional[datetime] = None,
    _execution_submitted_at: Optional[datetime] = None,
    _execution_bar_dt: Optional[datetime] = None,
    _skip_cash_gate: bool = False,
    _volume_budget: Optional[Dict[Tuple[str, str, datetime, str], float]] = None,
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
    # Keep inventory identity market+exchange+asset class scoped internally;
    # ``positions`` remains symbol-keyed for source compatibility.
    positions: Dict[str, float] = {str(k): float(v) for k, v in policy.initial_positions.items()}
    inventory = list(_initial_inventory_lots(policy))
    lot_counter = len(inventory)
    default_market = (policy.market or (policy.policy_ref.rsplit("-", 1)[-1] if policy.policy_ref else "UNKNOWN")).upper()
    for lot in policy.initial_lots:
        positions[lot.symbol] = positions.get(lot.symbol, 0.0) + float(lot.quantity)
    cash = float(policy.initial_cash)
    fills = []
    audits = []
    fee_calculator = None
    fee_schedule_enabled = policy.fee_calculator is not None or policy.fee_provider is not None
    volume_budget: Dict[Tuple[str, str, datetime, str], float] = (
        _volume_budget if _volume_budget is not None else {})
    if policy.fee_calculator is not None:
        fee_calculator = policy.fee_calculator
    elif policy.fee_provider is not None:
        fee_calculator = CostCalculator(policy.fee_provider)
    for index, intent in enumerate(ordered_intents):
        key_market = (intent.market or policy.market or "").upper()
        key_timeframe = (intent.timeframe or policy.timeframe or "").lower()
        candidates = [bar for key, values in by_key.items() if key[0] == intent.symbol.upper()
                      and (not key_market or key[1] == key_market)
                      and (not key_timeframe or key[2] == key_timeframe) for bar in values
                      if (_bar_instrument(bar) is not None and
                          _bar_instrument(bar)[0] == _instrument_identity(
                              intent.symbol, intent.market or policy.market or bar.market,
                              intent.exchange, intent.asset_class))]
        candidates.sort(key=lambda item: item.dt)
        signal_at = _execution_signal_at or intent.signal_at
        submitted_at = _execution_submitted_at or intent.submitted_at
        signal = next((bar for bar in candidates if bar.dt == signal_at), None)
        execution_candidate = (next((bar for bar in candidates if bar.dt == _execution_bar_dt), None)
                               if _execution_bar_dt is not None else None)
        if policy.calendar_required and signal is not None:
            _calendar_bar_gate(calendar_provider, signal)
        order_id = intent.order_id or f"order-{index + 1}"
        common = dict(order_id=order_id, symbol=intent.symbol, side=intent.side,
                      requested_quantity=float(intent.quantity), submitted_at=submitted_at,
                      source_trace=tuple(signal.source_trace if signal else ()), query_metadata=query_metadata)
        if fee_schedule_enabled and policy.fee_mode != "PER_FILL":
            common["query_metadata"] = {**common["query_metadata"],
                                         "fee_mode": policy.fee_mode,
                                         "fee_source": "fee_schedule"}
            audits.append(OrderAudit(status="unfilled", reason="FEE_MODE_UNSUPPORTED",
                                     eligible_at=None, fill_at=None,
                                     fee_mode=policy.fee_mode, fee_source="fee_schedule",
                                     **common)); continue
        is_limit = intent.order_type == "LIMIT"
        if is_limit:
            common["query_metadata"] = {**query_metadata, **_limit_metadata(
                intent, slippage_bps=float(policy.slippage_bps))}
        if signal is None:
            same_symbol_signal = next((bar for key, values in by_key.items()
                                       if key[0] == intent.symbol.upper()
                                       and (not key_timeframe or key[2] == key_timeframe)
                                       for bar in values if bar.dt == signal_at), None)
            reason = "BAR_IDENTITY_UNAVAILABLE" if same_symbol_signal is not None else "SIGNAL_BAR_MISSING"
            audits.append(OrderAudit(status="unfilled", reason=reason, eligible_at=None, fill_at=None, **common)); continue
        if not _bar_visible_at(signal, submitted_at):
            audits.append(OrderAudit(status="unfilled", reason="SIGNAL_BAR_NOT_CLOSED", eligible_at=signal.dt, fill_at=None, **common)); continue
        if policy.next_bar:
            eligible = next((bar for bar in candidates
                             if bar.dt > signal_at
                             and bar.dt >= submitted_at
                             and _bar_visible_at(bar, bar.dt)
                             and (not policy.calendar_required or _calendar_bar_gate(calendar_provider, bar) is None)), None)
        else:
            # Same-bar execution is strict for normal calls.  Retry calls use
            # a private candidate context while preserving the original order
            # signal/submission timestamps for visibility and audit semantics.
            # An explicitly selected retry bar is authoritative.  If it is
            # absent, never silently fill at the earlier signal timestamp.
            candidate = execution_candidate if _execution_bar_dt is not None else signal
            eligible = candidate if candidate is not None and (candidate.dt >= submitted_at if _execution_bar_dt is not None else candidate.dt == submitted_at) and _bar_visible_at(candidate, candidate.dt if _execution_bar_dt is not None else submitted_at) and (not policy.calendar_required or _calendar_bar_gate(calendar_provider, candidate) is None) else None
        if eligible is None:
            audits.append(OrderAudit(status="unfilled", reason="NO_NEXT_BAR", eligible_at=None, fill_at=None, **common)); continue
        expected_identity = _instrument_identity(intent.symbol, intent.market or policy.market or eligible.market,
                                                 intent.exchange, intent.asset_class)
        evidenced = _bar_instrument(eligible)
        if _identity_has_placeholder(expected_identity) or evidenced is None or evidenced[0] != expected_identity:
            audits.append(OrderAudit(status="unfilled", reason="BAR_IDENTITY_UNAVAILABLE", eligible_at=eligible.dt,
                                     fill_at=None, **common)); continue
        if evidenced[1] != policy.currency.upper():
            audits.append(OrderAudit(status="unfilled", reason="ORDER_CURRENCY_MISMATCH", eligible_at=eligible.dt,
                                     fill_at=None, **common)); continue
        # Currency and identity evidence are checked before any provider, fee,
        # cash, inventory or fill decision may mutate simulation state.
        if is_limit:
            # LIMIT orders consume only the eligible bar's OHLC evidence.  A
            # malformed range is distinct from a valid range that never trades.
            values = (eligible.o, eligible.h, eligible.l)
            try:
                ohlc = tuple(float(value) for value in values)
                valid_ohlc = all(math.isfinite(value) and value > 0 for value in ohlc)
                valid_ohlc = valid_ohlc and ohlc[2] <= ohlc[1]
            except (TypeError, ValueError):
                ohlc = (0.0, 0.0, 0.0)
                valid_ohlc = False
            if not valid_ohlc:
                audits.append(OrderAudit(status="unfilled", reason="LIMIT_DATA_MISSING", eligible_at=eligible.dt, fill_at=None, **common)); continue
            limit_price = float(intent.limit_price)
            if intent.side == "BUY":
                if ohlc[2] > limit_price:
                    audits.append(OrderAudit(status="unfilled", reason="LIMIT_NOT_REACHED", eligible_at=eligible.dt, fill_at=None, **common)); continue
                trigger = "OPEN_CROSS" if ohlc[0] <= limit_price else "LOW_TOUCH"
                raw_price = ohlc[0] if trigger == "OPEN_CROSS" else limit_price
            else:
                if ohlc[1] < limit_price:
                    audits.append(OrderAudit(status="unfilled", reason="LIMIT_NOT_REACHED", eligible_at=eligible.dt, fill_at=None, **common)); continue
                trigger = "OPEN_CROSS" if ohlc[0] >= limit_price else "HIGH_TOUCH"
                raw_price = ohlc[0] if trigger == "OPEN_CROSS" else limit_price
            common["query_metadata"] = {**common["query_metadata"], **_limit_metadata(intent, trigger=trigger, raw_price=raw_price)}
        else:
            if eligible.o is None or not math.isfinite(float(eligible.o)) or float(eligible.o) <= 0:
                audits.append(OrderAudit(status="unfilled", reason="DATA_GAP", eligible_at=eligible.dt, fill_at=None, **common)); continue
            raw_price = float(eligible.o)
            if intent.order_type == "STOP":
                if intent.side != "SELL" or intent.stop_price is None:
                    audits.append(OrderAudit(status="unfilled", reason="INVALID_STOP", eligible_at=eligible.dt, fill_at=None, **common)); continue
                # A gap-stop is deliberately restricted to STOP SELL.  It uses
                # the latest *visible* closed bar from the immediately prior
                # session.  Visibility is evaluated at the eligible bar's
                # timestamp; optional availability/close timestamps are merely
                # evidence gates when present (None means no extra evidence).
                gap_metadata = {}
                stop_triggered = eligible.o <= intent.stop_price or (
                    eligible.l is not None and eligible.l <= intent.stop_price
                )
                # A gap through the stop executes at the opening print. Only
                # an intrabar low crossing from above can use the stop level.
                raw_price = (float(eligible.o) if eligible.o <= intent.stop_price
                             else float(intent.stop_price))
                if policy.gap_stop_pct is not None and eligible.o < intent.stop_price:
                    prior_sessions = [bar.session_date for bar in candidates
                                       if bar.session_date < eligible.session_date]
                    previous = None
                    if prior_sessions:
                        prior_session = max(prior_sessions)
                        prior_bars = sorted(
                            (bar for bar in candidates if bar.session_date == prior_session),
                            key=lambda bar: bar.dt,
                            reverse=True,
                        )
                        # Walk backwards: a malformed/hidden final bar must not
                        # hide an earlier valid bar from the same prior session.
                        for candidate_previous in prior_bars:
                            try:
                                valid_close = (
                                    candidate_previous.c is not None
                                    and math.isfinite(float(candidate_previous.c))
                                    and float(candidate_previous.c) > 0
                                )
                            except (TypeError, ValueError):
                                valid_close = False
                            if valid_close and _bar_visible_at(candidate_previous, eligible.dt):
                                previous = candidate_previous
                                break
                    if previous is None:
                        gap_metadata = {"gap_stop_pct": float(policy.gap_stop_pct),
                                        "trigger": "GAP_PREVIOUS_CLOSE_MISSING",
                                        "raw_price": float(eligible.o)}
                        common["query_metadata"] = {**common["query_metadata"], **gap_metadata}
                        audits.append(OrderAudit(status="unfilled", reason="GAP_PREVIOUS_CLOSE_MISSING",
                                                 eligible_at=eligible.dt, fill_at=None, **common)); continue
                    common["source_trace"] = tuple(dict.fromkeys(
                        tuple(common["source_trace"]) + tuple(previous.source_trace)
                    ))
                    previous_close = float(previous.c)
                    gap_pct = (previous_close - float(eligible.o)) / previous_close
                    gap_metadata = {"previous_close": previous_close,
                                    "previous_close_dt": previous.dt.isoformat(),
                                    "previous_close_session": previous.session_date.isoformat(),
                                    "gap_pct": gap_pct,
                                    "gap_stop_pct": float(policy.gap_stop_pct),
                                    "trigger": "GAP_STOP_TRIGGERED" if gap_pct >= policy.gap_stop_pct else "STOP_TRIGGERED",
                                    "raw_price": raw_price}
                    common["query_metadata"] = {**common["query_metadata"], **gap_metadata}
                    if gap_pct >= policy.gap_stop_pct:
                        stop_triggered = True
                if not gap_metadata:
                    gap_metadata = {"gap_stop_pct": (float(policy.gap_stop_pct) if policy.gap_stop_pct is not None else None),
                                    "trigger": "STOP_TRIGGERED" if stop_triggered else "STOP_NOT_TRIGGERED",
                                    "raw_price": raw_price}
                    common["query_metadata"] = {**common["query_metadata"], **gap_metadata}
                if not stop_triggered:
                    audits.append(OrderAudit(status="unfilled", reason="STOP_NOT_TRIGGERED", eligible_at=eligible.dt, fill_at=None, **common)); continue
        if market_rule_provider is not None or policy.market_rules_required:
            rule_provider = market_rule_provider or MarketRuleProvider(
                status_provider=status_provider, limit_provider=limit_provider,
                market=intent.market or policy.market or getattr(eligible, "market", "")
            )
            rule_decision = rule_provider.decide(intent.symbol, eligible.dt, eligible, intent.side, raw_price)
            common["source_trace"] = tuple(dict.fromkeys(tuple(common["source_trace"]) + tuple(rule_decision.source_trace)))
            common["query_metadata"] = {**common["query_metadata"], "rule_ref": rule_decision.rule_ref,
                                         "rule_version": rule_decision.rule_version,
                                         "rule_snapshot": rule_decision.snapshot,
                                         "rule_source_trace": list(rule_decision.source_trace)}
            if not rule_decision.allowed:
                audits.append(OrderAudit(status="unfilled", reason=rule_decision.reason, eligible_at=eligible.dt, fill_at=None, **common)); continue
        else:
            status_reason = _status_reason(
                _provider_call(status_provider, intent.symbol, eligible.dt, eligible)
                if status_provider is not None else None
            )
            if status_reason:
                audits.append(OrderAudit(status="unfilled", reason=status_reason, eligible_at=eligible.dt, fill_at=None, **common)); continue
            if limit_provider is not None:
                limit_reason = _limit_reason(_provider_call(limit_provider, intent.symbol, eligible.dt, eligible), intent.side, raw_price)
                if limit_reason:
                    audits.append(OrderAudit(status="unfilled", reason=limit_reason, eligible_at=eligible.dt, fill_at=None, **common)); continue
        requested_qty = _quantity(intent.quantity, policy.lot_size)
        if requested_qty <= 0:
            audits.append(OrderAudit(status="unfilled", reason="BELOW_LOT", eligible_at=eligible.dt, fill_at=None, **common)); continue

        # Explicit fill models are opt-in.  Keep this branch isolated so the
        # historical all-or-none path below remains byte-for-byte equivalent
        # for callers that leave ``fill_model`` unset.
        decision: Optional[FillDecision] = None
        filled_quantity = requested_qty
        model_price = raw_price
        model_ref: Optional[str] = None
        model_version: Optional[str] = None
        model_reason: Optional[str] = None
        model_source_trace: Tuple[str, ...] = ()
        volume_key: Optional[Tuple[str, str, datetime, str]] = None
        if isinstance(policy.fill_model, VolumeParticipationFillModel):
            # Bar has no exchange/asset-class field: its displayed liquidity is
            # shared by every order mapped onto that very symbol/market/time bar.
            volume_key = (eligible.market.upper(), eligible.symbol.upper(),
                          eligible.dt, eligible.timeframe.lower())
            if volume_key not in volume_budget:
                volume = float(eligible.v) if eligible.v is not None else 0.0
                volume_budget[volume_key] = math.floor(
                    volume * policy.fill_model.participation_rate / policy.fill_model.lot_size
                ) * policy.fill_model.lot_size if math.isfinite(volume) and volume > 0 else 0.0
        if policy.fill_model is not None:
            evaluator = getattr(policy.fill_model, "evaluate", None)
            if not callable(evaluator):
                evaluator = getattr(policy.fill_model, "decide")
            if volume_key is not None:
                decision = policy.fill_model.decide_with_budget(
                    intent, eligible, requested_qty, raw_price,
                    volume_budget[volume_key])
            else:
                decision = evaluator(intent, eligible, requested_qty, raw_price)
            if not isinstance(decision, FillDecision):
                raise TypeError("fill_model must return FillDecision")
            decision_qty = float(decision.quantity)
            model_ref = decision.model_ref
            model_version = decision.model_version
            model_reason = decision.reason_code
            model_source_trace = tuple(decision.source_trace)
            common["source_trace"] = tuple(dict.fromkeys(tuple(common["source_trace"]) + model_source_trace))
            if (not math.isfinite(decision_qty) or decision_qty < 0
                    or decision_qty > requested_qty + 1e-9
                    or (decision_qty > 0 and abs(decision_qty / policy.lot_size - round(decision_qty / policy.lot_size)) > 1e-9)
                    or (decision.price is not None and (not math.isfinite(float(decision.price)) or float(decision.price) <= 0))):
                audits.append(OrderAudit(status="unfilled", reason="INVALID_FILL_DECISION", eligible_at=eligible.dt,
                                          fill_at=None, fill_model_ref=model_ref, fill_model_version=model_version,
                                          fill_reason="INVALID_FILL_DECISION", fill_source_trace=model_source_trace,
                                          filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                          **common)); continue
            common["query_metadata"] = {**common["query_metadata"], "fill_model_ref": model_ref,
                                         "fill_model_version": model_version,
                                         "fill_model_metadata": _json_value(decision.metadata),
                                         "fill_source_trace": list(model_source_trace)}
            filled_quantity = decision_qty
            if decision.status in {"UNFILLED", "REJECTED"}:
                filled_quantity = 0.0
            if filled_quantity <= 0:
                audits.append(OrderAudit(status="unfilled", reason="NO_LIQUIDITY", eligible_at=eligible.dt,
                                          fill_at=None, fill_model_ref=model_ref,
                                          fill_model_version=model_version, fill_reason="NO_LIQUIDITY",
                                          fill_source_trace=model_source_trace, filled_quantity=0.0,
                                          remaining_quantity=float(intent.quantity), fill_count=0, **common)); continue
            if decision.price is None:
                audits.append(OrderAudit(status="unfilled", reason="NO_LIQUIDITY", eligible_at=eligible.dt,
                                          fill_at=None, fill_model_ref=model_ref,
                                          fill_model_version=model_version, fill_reason="NO_LIQUIDITY",
                                          fill_source_trace=model_source_trace, filled_quantity=0.0,
                                          remaining_quantity=float(intent.quantity), fill_count=0, **common)); continue
            model_price = float(decision.price)

        if is_limit and ((intent.side == "BUY" and model_price > float(intent.limit_price))
                         or (intent.side == "SELL" and model_price < float(intent.limit_price))):
            common["query_metadata"] = {**common["query_metadata"], **_limit_metadata(
                intent, trigger=trigger, raw_price=raw_price, model_price=model_price,
                slippage_bps=float(policy.slippage_bps))}
            audits.append(OrderAudit(status="unfilled", reason="LIMIT_MODEL_PRICE_BREACH", eligible_at=eligible.dt,
                                      fill_at=None, fill_model_ref=model_ref, fill_model_version=model_version,
                                      fill_reason="LIMIT_MODEL_PRICE_BREACH", fill_source_trace=model_source_trace,
                                      filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                      **common)); continue

        effective_quantity = filled_quantity if decision is not None else requested_qty
        quantity = effective_quantity
        if intent.side == "SELL":
            sell_identity = _instrument_identity(
                intent.symbol, intent.market or policy.market or default_market,
                intent.exchange, intent.asset_class
            )
            matching = [lot for lot in inventory if _instrument_identity(
                lot.symbol, lot.market, lot.exchange, lot.asset_class
            ) == sell_identity]
            total_inventory = sum(float(lot.quantity) for lot in matching)
            if quantity > total_inventory + 1e-9:
                audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_POSITION", eligible_at=eligible.dt, fill_at=None,
                                          fill_model_ref=model_ref, fill_model_version=model_version,
                                          fill_reason="INSUFFICIENT_POSITION", fill_source_trace=model_source_trace,
                                          filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                          **common)); continue
            if policy.t_plus_one:
                known_available = sum(float(lot.quantity) for lot in matching
                                      if lot.available_session is not None and lot.available_session <= eligible.session_date)
                has_unknown_age = any(lot.available_session is None for lot in matching)
                if quantity > known_available + 1e-9:
                    inventory_reason = "INVENTORY_AGE_UNKNOWN" if has_unknown_age and matching else "T_PLUS_ONE"
                    audits.append(OrderAudit(status="unfilled", reason=inventory_reason, eligible_at=eligible.dt, fill_at=None,
                                              fill_model_ref=model_ref, fill_model_version=model_version,
                                              fill_reason=inventory_reason, fill_source_trace=model_source_trace,
                                              filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                              **common)); continue
        direction = 1.0 if intent.side == "BUY" else -1.0
        # FillDecision.price is the model/reference price; apply engine
        # slippage exactly once to that price for explicit models.
        slippage_reference = model_price if decision is not None else raw_price
        slipped_price = slippage_reference * (1.0 + direction * float(policy.slippage_bps) / 10000.0)
        price = slipped_price
        slippage_clamped = False
        if is_limit:
            limit_price = float(intent.limit_price)
            if intent.side == "BUY":
                price = min(slipped_price, limit_price)
            else:
                price = max(slipped_price, limit_price)
            slippage_clamped = not math.isclose(price, slipped_price, rel_tol=0.0, abs_tol=1e-12)
            common["query_metadata"] = {**common["query_metadata"], **_limit_metadata(
                intent, trigger=trigger, raw_price=raw_price, model_price=model_price,
                slipped_price=slipped_price, final_price=price,
                slippage_bps=float(policy.slippage_bps), slippage_clamped=slippage_clamped)}
        # Resolve fees only after accepted quantity and final (slipped/clamped)
        # price are known.  A configured schedule is strictly PER_FILL and is
        # never mixed with the legacy fee_bps calculation.
        fee_mode = "PER_FILL" if fee_schedule_enabled else "LEGACY_BPS"
        fee_source = "fee_schedule" if fee_schedule_enabled else "legacy_bps"
        fee_schedule_ref = fee_schedule_version = fee_schedule_identity = fee_schedule_hash = None
        fee_breakdown: Mapping[str, Any] = {}
        if fee_schedule_enabled:
            fee_error = None
            try:
                market_name = str(intent.market or policy.market or eligible.market).strip()
                _, exchange_name, asset_class_name, _ = _instrument_identity(
                    intent.symbol, market_name, intent.exchange, intent.asset_class
                )
                breakdown = fee_calculator.calculate(
                    side=intent.side, quantity=quantity, price=price, at=eligible.dt,
                    market=market_name, exchange=exchange_name,
                    asset_class=asset_class_name, currency=policy.currency,
                )
                if not isinstance(breakdown, CostBreakdown):
                    raise TypeError("fee calculator must return CostBreakdown")
                fee_breakdown = _json_value(breakdown.as_dict())
                fee_schedule_ref = str(breakdown.schedule_ref)
                fee_schedule_version = str(breakdown.version)
                fee_schedule_identity = f"{fee_schedule_ref}@{fee_schedule_version}"
                fee_schedule_hash = getattr(breakdown, "schedule_hash", None)
                gross = float(breakdown.gross_value)
                fee = float(breakdown.total)
                cash_delta = float(breakdown.cash_delta)
                if not all(math.isfinite(value) for value in (gross, fee, cash_delta)):
                    raise ValueError("fee breakdown contains non-finite values")
            except Exception as exc:
                fee_error = str(exc) or exc.__class__.__name__
            if fee_error is not None:
                fee_metadata = {"fee_mode": fee_mode, "fee_source": fee_source,
                                "fee_error": fee_error}
                common["query_metadata"] = {**common["query_metadata"], **fee_metadata}
                audits.append(OrderAudit(status="unfilled", reason="FEE_SCHEDULE_UNAVAILABLE",
                                          eligible_at=eligible.dt, fill_at=None,
                                          fill_model_ref=model_ref, fill_model_version=model_version,
                                          fill_reason="FEE_SCHEDULE_UNAVAILABLE",
                                          fill_source_trace=model_source_trace,
                                          filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                          fee_mode=fee_mode, fee_source=fee_source,
                                          fee_breakdown={}, **common)); continue
            fee_metadata = {"fee_mode": fee_mode, "fee_source": fee_source,
                            "fee_schedule_ref": fee_schedule_ref,
                            "fee_schedule_version": fee_schedule_version,
                            "fee_schedule_identity": fee_schedule_identity,
                            "fee_schedule_hash": fee_schedule_hash,
                            "fee_breakdown": fee_breakdown}
            common["query_metadata"] = {**common["query_metadata"], **_json_value(fee_metadata)}
            total = gross + fee if intent.side == "BUY" else gross - fee
        else:
            gross = price * quantity
            fee = gross * float(policy.fee_bps) / 10000.0
            total = gross + fee if intent.side == "BUY" else gross - fee
            cash_delta = -total if intent.side == "BUY" else total
            fee_metadata = {"fee_mode": fee_mode, "fee_source": fee_source}
            common["query_metadata"] = {**common["query_metadata"], **fee_metadata}
        if intent.side == "BUY" and not _skip_cash_gate and (-cash_delta) > cash + 1e-9:
            audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_CASH", eligible_at=eligible.dt, fill_at=None,
                                      fill_model_ref=model_ref, fill_model_version=model_version,
                                      fill_reason="INSUFFICIENT_CASH", fill_source_trace=model_source_trace,
                                      filled_quantity=0.0, remaining_quantity=float(intent.quantity), fill_count=0,
                                      fee_mode=fee_mode, fee_source=fee_source,
                                      fee_schedule_ref=fee_schedule_ref, fee_schedule_version=fee_schedule_version,
                                      fee_schedule_identity=fee_schedule_identity, fee_schedule_hash=fee_schedule_hash,
                                      fee_breakdown=fee_breakdown, **common)); continue
        cash += cash_delta
        if volume_key is not None:
            volume_budget[volume_key] = max(0.0, volume_budget[volume_key] - quantity)
        positions[intent.symbol] = positions.get(intent.symbol, 0.0) + (quantity if intent.side == "BUY" else -quantity)
        if intent.side == "SELL":
            remaining = quantity
            sell_identity = _instrument_identity(
                intent.symbol, intent.market or policy.market or default_market,
                intent.exchange, intent.asset_class
            )
            candidates_lots = sorted(
                (lot for lot in inventory
                 if _instrument_identity(lot.symbol, lot.market, lot.exchange, lot.asset_class)
                 == sell_identity
                 and (not policy.t_plus_one or (lot.available_session is not None and lot.available_session <= eligible.session_date))),
                key=lambda lot: (lot.acquisition_session is None, lot.acquisition_session or date.max, lot.lot_id),
            )
            for lot in candidates_lots:
                consumed = min(float(lot.quantity), remaining)
                remaining -= consumed
                inventory[inventory.index(lot)] = InventoryLot(
                    lot.symbol, lot.market, lot.exchange, lot.asset_class, lot.acquisition_session,
                    lot.available_session, float(lot.quantity) - consumed, lot.lot_id,
                ) if float(lot.quantity) > consumed else None
                if remaining <= 1e-9:
                    break
            inventory[:] = [lot for lot in inventory if lot is not None and lot.quantity > 1e-9]
        if intent.side == "BUY":
            market, exchange, asset_class, _ = _instrument_identity(
                intent.symbol, intent.market or policy.market or default_market, intent.exchange, intent.asset_class
            )
            available_session = eligible.session_date
            if policy.t_plus_one:
                if calendar_provider is None:
                    available_session = eligible.session_date + timedelta(days=1)
                else:
                    try:
                        available_session = calendar_provider.next_open_session(eligible.session_date, inclusive=False)
                    except Exception:
                        # A production calendar must not synthesize a trading day.
                        # Keep the fill auditable, but leave settlement unknown so
                        # a later sell is fail-closed with INVENTORY_AGE_UNKNOWN.
                        available_session = None
            lot_counter += 1
            inventory.append(InventoryLot(intent.symbol, market, exchange, asset_class, eligible.session_date,
                                          available_session if policy.t_plus_one else eligible.session_date,
                                          quantity, f"fill-{order_id}-{lot_counter}"))
        if decision is None:
            if intent.order_type == "STOP":
                fill_reason = str(common["query_metadata"].get("trigger", "STOP_TRIGGERED"))
                if fill_reason not in {"STOP_TRIGGERED", "GAP_STOP_TRIGGERED"}:
                    fill_reason = "STOP_TRIGGERED"
            else:
                fill_reason = "FILLED"
        else:
            # Explicit partial semantics take precedence over quantity alone;
            # a short quantity is partial even if the model says FILLED.
            explicit_partial = decision.status == "PARTIAL" or model_reason == "PARTIAL_FILL"
            fill_reason = "PARTIAL_FILL" if explicit_partial or quantity < requested_qty else "FILLED"
            if intent.order_type == "STOP":
                trigger_reason = str(common["query_metadata"].get("trigger", "STOP_TRIGGERED"))
                fill_reason = (trigger_reason if trigger_reason in {"STOP_TRIGGERED", "GAP_STOP_TRIGGERED"}
                               else "STOP_TRIGGERED")
        # STOP completion is a quantity fact, independent of its trigger reason.
        # Other orders preserve their historical explicit-partial semantics.
        fill_status = ("filled" if quantity >= float(intent.quantity) - 1e-9 else "partial"
                       ) if intent.order_type == "STOP" else (
                           "filled" if fill_reason == "FILLED" else "partial")
        fills.append(Fill(order_id, intent.symbol, intent.side, quantity, price, gross, fee,
                          abs(price - slippage_reference) * quantity, eligible.dt, eligible.source,
                          model_ref, model_version, fill_reason, model_source_trace,
                          fee_mode, fee_source, fee_schedule_ref, fee_schedule_version,
                          fee_schedule_identity, fee_schedule_hash, fee_breakdown))
        audits.append(OrderAudit(status=fill_status, reason=fill_reason, eligible_at=eligible.dt,
                                 fill_at=eligible.dt, fill_model_ref=model_ref,
                                 fill_model_version=model_version, fill_reason=fill_reason,
                                 fill_source_trace=model_source_trace, filled_quantity=quantity,
                                 remaining_quantity=max(0.0, float(intent.quantity) - quantity),
                                 fill_count=1, fee_mode=fee_mode, fee_source=fee_source,
                                 fee_schedule_ref=fee_schedule_ref, fee_schedule_version=fee_schedule_version,
                                 fee_schedule_identity=fee_schedule_identity, fee_schedule_hash=fee_schedule_hash,
                                 fee_breakdown=fee_breakdown, **common))
    equity, instrument_positions = _value_inventory(store, selected_query, cash, tuple(inventory), policy.currency)
    partial_count = sum(1 for audit in audits if audit.status == "partial")
    fully_filled_count = sum(1 for audit in audits if audit.status == "filled")
    # Fill quantities include historical fills whose appended audit field
    # retains its compatibility default of zero.
    filled_quantity_total = sum(float(fill.quantity) for fill in fills)
    partial_filled_qty = sum(float(audit.filled_quantity) for audit in audits if audit.status == "partial")
    remaining_quantity_total = sum(
        float(audit.remaining_quantity if audit.remaining_quantity is not None else 0.0)
        for audit in audits
    )
    unfilled_orders = sum(1 for audit in audits if audit.status != "filled")
    # ``filled`` is a historical order-count metric backed by emitted Fill
    # records; partial fills still emit a Fill and must remain counted.  Keep
    # the stricter fully-filled order count under its explicit new key.
    metrics = {"submitted": len(ordered_intents), "filled": len(fills),
               "unfilled": unfilled_orders,
               "unfilled_rate": unfilled_orders / len(ordered_intents) if ordered_intents else 0.0,
               "partial_count": partial_count,
               "fully_filled_count": fully_filled_count,
               "filled_quantity": filled_quantity_total,
               "partial_filled_qty": partial_filled_qty,
               "remaining_quantity": remaining_quantity_total}
    return ExecutionResult(tuple(fills), tuple(audits), cash, dict(positions), equity,
                           _json_value({**store.explain(), **({"calendar": calendar_identity} if calendar_identity else {})}), query_metadata, ordered_intents, metrics, SCHEMA,
                           tuple(inventory), dict(instrument_positions))


def _execution_window(
    intent: OrderIntent, policy: ExecutionConfig, bars: Tuple[Bar, ...]
) -> Optional[Tuple[datetime, datetime]]:
    """Bound possible order attempts without predicting fills or liquidity.

    GTC attempts consume matching bars even when an attempt cannot fill.  An
    unbounded order remains live through the last available candidate; the
    absence of ``expire_at`` is never treated as an early terminal event.
    """
    matching = tuple(bar for bar in bars
                     if bar.symbol.upper() == intent.symbol.upper()
                     and (_bar_instrument(bar) is not None and
                          _bar_instrument(bar)[0] == _instrument_identity(
                              intent.symbol, intent.market or policy.market or bar.market,
                              intent.exchange, intent.asset_class))
                     and (not policy.market or bar.market.upper() == policy.market.upper())
                     and (not intent.market or bar.market.upper() == intent.market.upper())
                     and (not policy.timeframe or bar.timeframe.lower() == policy.timeframe.lower())
                     and (not intent.timeframe or bar.timeframe.lower() == intent.timeframe.lower()))
    signal = next((bar for bar in matching if bar.dt == intent.signal_at), None)
    if signal is None or not _bar_visible_at(signal, intent.submitted_at):
        return None
    if intent.time_in_force == "GTC":
        candidates = tuple(bar for bar in matching
                           if bar.dt > intent.signal_at and bar.dt >= intent.submitted_at
                           and (intent.expire_at is None or bar.dt <= intent.expire_at))
        candidates = candidates[:intent.max_attempts] if intent.max_attempts is not None else candidates
        return (candidates[0].dt, candidates[-1].dt) if candidates else None
    if policy.next_bar:
        candidates = tuple(bar for bar in matching
                           if bar.dt > intent.signal_at and bar.dt >= intent.submitted_at
                           and _bar_visible_at(bar, bar.dt))
        return (candidates[0].dt, candidates[0].dt) if candidates else None
    if signal.dt == intent.submitted_at:
        return signal.dt, signal.dt
    return None


def _temporal_batch_conflict(
    store: BarStore, ordered: Tuple[OrderIntent, ...], policy: ExecutionConfig,
    query: Optional[BarQuery]
) -> bool:
    """Reject a batch if sequential processing could apply future cash first."""
    bars = store.query(query or BarQuery())
    latest_prior: Optional[datetime] = None
    for intent in ordered:
        window = _execution_window(intent, policy, bars)
        if window is None:
            continue
        if latest_prior is not None and latest_prior > window[0]:
            return True
        latest_prior = max(latest_prior, window[1]) if latest_prior is not None else window[1]
    return False


def run_from_store(
    store: BarStore,
    intents: Iterable[OrderIntent],
    config: Optional[ExecutionConfig] = None,
    *,
    query: Optional[BarQuery] = None,
    limit_provider: Optional[Provider] = None,
    status_provider: Optional[Provider] = None,
    calendar_provider: Optional[Any] = None,
    market_rule_provider: Optional[MarketRuleProvider] = None,
) -> ExecutionResult:
    """Execute orders, retaining legacy one-shot behavior and GTC retry support.

    The existing engine remains the authoritative gate implementation.  DAY orders
    execute through the historical one-pass path.  GTC orders are retried on later
    bars by invoking the same path for each remaining slice; fills and cash are
    accumulated only after each accepted slice, while the returned audit is merged
    to one order-level record.
    """
    policy = config or ExecutionConfig()
    ordered = tuple(sorted(tuple(intents),
                           key=lambda item: (item.signal_at, item.submitted_at, item.order_id)))
    # This path processes complete orders sequentially rather than bar-by-bar.
    # Refuse a batch before touching either cash or inventory if the possible
    # attempt window of a prior order crosses a later order's first attempt.
    # Determine windows from candidate bars, not actual fills: a failed first
    # attempt may still fill on a later retry.
    if len(ordered) > 1 and _temporal_batch_conflict(store, ordered, policy, query):
        baseline = _run_from_store_once(
            store, (), policy, query=query, calendar_provider=calendar_provider)
        reason = "CROSS_ORDER_TEMPORAL_CONFLICT"
        audits = tuple(OrderAudit(
            order_id=intent.order_id or f"order-{index + 1}",
            symbol=intent.symbol, side=intent.side,
            requested_quantity=float(intent.quantity), status="unfilled",
            reason=reason, submitted_at=intent.submitted_at,
            eligible_at=None, fill_at=None,
            remaining_quantity=float(intent.quantity), fill_reason=reason,
            query_metadata={"error_code": reason, "time_in_force": intent.time_in_force},
        ) for index, intent in enumerate(ordered))
        metrics = {"submitted": len(ordered), "filled": 0, "unfilled": len(ordered),
                   "unfilled_rate": 1.0, "partial_count": 0, "fully_filled_count": 0,
                   "filled_quantity": 0.0, "partial_filled_qty": 0.0,
                   "remaining_quantity": sum(float(intent.quantity) for intent in ordered)}
        return replace(baseline, audits=audits, orders=ordered, metrics=metrics)
    if not any(item.time_in_force == "GTC" for item in ordered) and policy.fee_mode != "PER_ORDER":
        return _run_from_store_once(store, ordered, policy, query=query,
                                    limit_provider=limit_provider, status_provider=status_provider,
                                    calendar_provider=calendar_provider, market_rule_provider=market_rule_provider)
    # Retry implementation deliberately uses one intent at a time to preserve
    # inventory/cash gates.  PER_ORDER runs each attempt through the existing
    # PER_FILL gates, then applies one aggregate fee delta at order close.
    all_fills = []
    all_audits = []
    remaining_cash = policy.initial_cash
    remaining_lots = _initial_inventory_lots(policy)
    remaining_positions: Dict[str, float] = dict(policy.initial_positions)
    for lot in policy.initial_lots:
        remaining_positions[lot.symbol] = remaining_positions.get(lot.symbol, 0.0) + float(lot.quantity)
    volume_budget: Dict[Tuple[str, str, datetime, str], float] = {}
    per_order = policy.fee_mode == "PER_ORDER" and (policy.fee_calculator is not None or policy.fee_provider is not None)
    if policy.fee_mode == "PER_ORDER" and not per_order:
        baseline = _run_from_store_once(
            store, (), policy, query=query, calendar_provider=calendar_provider)
        equity, instrument_positions = _value_inventory(
            store, query, float(policy.initial_cash), remaining_lots, policy.currency)
        audits = tuple(OrderAudit(order_id=intent.order_id or f"order-{index + 1}", symbol=intent.symbol,
                                  side=intent.side, requested_quantity=float(intent.quantity),
                                  status="unfilled", reason="FEE_CONFIG_UNSUPPORTED",
                                  submitted_at=intent.submitted_at, eligible_at=None, fill_at=None)
                       for index, intent in enumerate(ordered))
        return replace(baseline, audits=audits, orders=ordered,
                       equity=equity, instrument_positions=instrument_positions,
                       metrics={"submitted": len(ordered), "filled": 0, "unfilled": len(ordered),
                                "unfilled_rate": 1.0 if ordered else 0.0, "partial_count": 0,
                                "fully_filled_count": 0, "filled_quantity": 0.0,
                                "partial_filled_qty": 0.0,
                                "remaining_quantity": sum(float(item.quantity) for item in ordered)})
    calculator = policy.fee_calculator or (CostCalculator(policy.fee_provider) if policy.fee_provider is not None else None)

    def aggregate_order_fee(order_intent: OrderIntent, result: ExecutionResult,
                            accumulator: Optional[OrderFeeAccumulator] = None,
                            prior_aggregate_fee: float = 0.0,
                            all_order_fills: Optional[list] = None) -> Tuple[ExecutionResult, Optional[OrderFeeAccumulator], float]:
        """Replace per-attempt fee charges with one order-level allocation."""
        if not per_order or calculator is None or not result.fills:
            return result, accumulator, prior_aggregate_fee
        provider = getattr(calculator, "provider", None)
        if provider is None or not hasattr(provider, "resolve"):
            raise ValueError("FEE_SCHEDULE_UNAVAILABLE: PER_ORDER requires FeeScheduleProvider")
        first = result.fills[0]
        market_name = str(order_intent.market or policy.market or "CN")
        _, exchange_name, asset_class_name, _ = _instrument_identity(
            order_intent.symbol, market_name, order_intent.exchange, order_intent.asset_class)
        schedule = provider.resolve(market=market_name, exchange=exchange_name,
                                    asset_class=asset_class_name, currency=policy.currency,
                                    at=first.dt)
        if accumulator is None:
            accumulator = OrderFeeAccumulator(schedule, side=order_intent.side)
        else:
            if accumulator.schedule_hash != schedule.schedule_hash or accumulator.schedule_identity != schedule.identity:
                raise ValueError("FEE_SCHEDULE_UNAVAILABLE: schedule identity/hash mismatch")
            # Work on a copy: a rejected attempt cannot change fees already charged
            # to earlier accepted fills of this GTC order.
            prior_fills = accumulator.fills
            accumulator = OrderFeeAccumulator(schedule, side=order_intent.side)
            for quantity, price in prior_fills:
                accumulator.add_fill(quantity, price, schedule=schedule, schedule_hash=schedule.schedule_hash)
        for item in result.fills:
            accumulator.add_fill(item.quantity, item.price, schedule=schedule, schedule_hash=schedule.schedule_hash)
        allocations = accumulator.finalize()
        aggregate_fee = float(accumulator.total)
        prior_fee = sum(float(item.fee) for item in result.fills)
        corrected_cash = result.cash + prior_fee - (aggregate_fee - prior_aggregate_fee)
        if corrected_cash < -1e-9:
            raise ValueError("INSUFFICIENT_CASH")
        # Reallocate every fill in the order.  The accumulator may revise earlier
        # minimum/rounding allocations when a later partial fill is appended.
        target_fills = (tuple(all_order_fills) if all_order_fills is not None else tuple(result.fills))
        corrected_fills = tuple(replace(item, fee=float(allocation.total), fee_mode="PER_ORDER",
                                        fee_source="fee_schedule", fee_schedule_ref=allocation.schedule_ref,
                                        fee_schedule_version=allocation.version,
                                        fee_schedule_identity=f"{allocation.schedule_ref}@{allocation.version}",
                                        fee_schedule_hash=allocation.schedule_hash,
                                        fee_breakdown=allocation.as_dict())
                                for item, allocation in zip(target_fills, allocations))
        corrected_current = corrected_fills[-len(result.fills):]
        if all_order_fills is not None:
            all_order_fills[:] = list(corrected_fills)
        aggregate = accumulator.aggregate_breakdown()
        breakdown = _json_value(aggregate.as_dict()) if aggregate is not None else {}
        corrected_audits = tuple(replace(item, fee_mode="PER_ORDER", fee_source="fee_schedule",
                                         fee_schedule_ref=schedule.schedule_ref, fee_schedule_version=schedule.version,
                                         fee_schedule_identity=schedule.identity, fee_schedule_hash=schedule.schedule_hash,
                                         fee_breakdown=breakdown,
                                         query_metadata={**item.query_metadata, "fee_mode": "PER_ORDER",
                                                         "fee_source": "fee_schedule",
                                                         "fee_schedule_identity": schedule.identity,
                                                         "fee_schedule_hash": schedule.schedule_hash,
                                                         "fee_breakdown": breakdown})
                                  for item in result.audits)
        return replace(result, fills=corrected_current, audits=corrected_audits, cash=corrected_cash), accumulator, aggregate_fee
    for intent in ordered:
        # Retry metadata is scoped to one order; never leak attempts between orders.
        retry_trace = []
        if intent.time_in_force != "GTC":
            one = _run_from_store_once(store, (intent,), replace(policy,
                                                                 fee_mode="PER_FILL" if per_order else policy.fee_mode,
                                                                 initial_cash=remaining_cash,
                                                                 initial_positions={},
                                                                 initial_lots=remaining_lots), query=query,
                                        limit_provider=limit_provider, status_provider=status_provider,
                                        calendar_provider=calendar_provider, market_rule_provider=market_rule_provider,
                                        _volume_budget=volume_budget)
            if per_order:
                one, _, _ = aggregate_order_fee(intent, one)
            all_fills.extend(one.fills); all_audits.extend(one.audits)
            remaining_cash = one.cash; remaining_positions = dict(one.positions); remaining_lots = one.inventory_lots
            continue
        candidates = tuple(bar for bar in store.query(query or BarQuery())
                           if bar.symbol.upper() == intent.symbol.upper()
                           and (_bar_instrument(bar) is not None and
                                _bar_instrument(bar)[0] == _instrument_identity(
                                    intent.symbol, intent.market or policy.market or bar.market,
                                    intent.exchange, intent.asset_class))
                           and (not policy.market or bar.market.upper() == policy.market.upper())
                           and (not intent.market or bar.market.upper() == intent.market.upper())
                           and (not policy.timeframe or bar.timeframe.lower() == policy.timeframe.lower())
                           and (not intent.timeframe or bar.timeframe.lower() == intent.timeframe.lower())
                           and bar.dt > intent.signal_at
                           and bar.dt >= intent.submitted_at)
        candidates = tuple(sorted(candidates, key=lambda bar: bar.dt))
        max_attempts = intent.max_attempts or len(candidates)
        if intent.expire_at is not None:
            candidates = tuple(bar for bar in candidates if bar.dt <= intent.expire_at)
        candidates = candidates[:max_attempts]
        filled = 0.0
        last_result = None
        terminal_failure_reason: Optional[str] = None
        order_accumulator: Optional[OrderFeeAccumulator] = None
        order_aggregate_fee = 0.0
        order_fills = []
        order_all_start = len(all_fills)
        state = OrderFillState(intent.order_id or f"order-{len(all_audits) + 1}", float(intent.quantity),
                               time_in_force=intent.time_in_force)
        for bar in candidates:
            if filled >= float(intent.quantity) - 1e-9:
                break
            retry_intent = replace(intent, quantity=float(intent.quantity) - filled,
                                   time_in_force="GTC", max_attempts=None, expire_at=None)
            # A retry is evaluated against this candidate bar itself.  The
            # one-shot engine's same-bar policy keeps all visibility/calendar/
            # rule/LIMIT-STOP/fill gates active without skipping the candidate.
            attempt_kwargs = {"next_bar": False, "initial_cash": remaining_cash,
                              "fee_mode": "PER_FILL" if per_order else policy.fee_mode,
                              "initial_positions": {}, "initial_lots": remaining_lots}
            attempt_policy = replace(policy, **attempt_kwargs)
            prior_volume_budget = dict(volume_budget)
            result = _run_from_store_once(store, (retry_intent,), attempt_policy,
                                          query=query, limit_provider=limit_provider,
                                          status_provider=status_provider, calendar_provider=calendar_provider,
                                          market_rule_provider=market_rule_provider,
                                          _execution_signal_at=intent.signal_at,
                                          _execution_submitted_at=intent.submitted_at,
                                          _execution_bar_dt=bar.dt,
                                          _skip_cash_gate=per_order,
                                          _volume_budget=volume_budget)
            if per_order and result.fills:
                order_fills.extend(result.fills)
            if per_order:
                try:
                    result, order_accumulator, order_aggregate_fee = aggregate_order_fee(
                        retry_intent, result, order_accumulator, order_aggregate_fee,
                        order_fills)
                except Exception as exc:
                    failure_reason = ("INSUFFICIENT_CASH" if isinstance(exc, ValueError) and str(exc) == "INSUFFICIENT_CASH"
                                      else "FEE_SCHEDULE_CHANGED" if "mismatch" in str(exc).lower()
                                      else "FEE_SCHEDULE_UNAVAILABLE")
                    terminal_failure_reason = failure_reason
                    volume_budget.clear()
                    volume_budget.update(prior_volume_budget)
                    # Only this attempt is rejected. Earlier accepted fills and their
                    # charged aggregate fee remain committed; no later retry is allowed.
                    if result.fills:
                        del order_fills[-len(result.fills):]
                    result = replace(result, fills=(), cash=remaining_cash,
                                     positions=dict(remaining_positions), inventory_lots=tuple(remaining_lots),
                                     audits=(replace(result.audits[-1], status="unfilled", reason=failure_reason,
                                                     filled_quantity=0.0, remaining_quantity=float(intent.quantity) - filled,
                                                     fill_count=0, fill_at=None, fill_reason=failure_reason,
                                                     query_metadata={**result.audits[-1].query_metadata,
                                                                     "fee_error": failure_reason})
                                             if result.audits else OrderAudit(
                                                 order_id=intent.order_id or "order", symbol=intent.symbol, side=intent.side,
                                                 requested_quantity=float(intent.quantity), status="unfilled", reason=failure_reason,
                                                 submitted_at=intent.submitted_at, eligible_at=None, fill_at=None),))
            last_result = result
            retry_reason = result.audits[0].reason if result.audits else "NO_AUDIT"
            retry_trace.append({"bar_dt": bar.dt, "reason": retry_reason,
                                "status": result.audits[0].status if result.audits else "unfilled"})
            state.attempts += 1
            terminal_failure_reason = retry_reason
            if result.fills:
                all_fills.extend(result.fills)
                if per_order:
                    all_fills[order_all_start:] = list(order_fills)
                accepted = sum(item.quantity for item in result.fills)
                filled += accepted
                state.accept_fill(accepted, result.fills[-1].dt,
                                  tuple(trace for item in result.fills for trace in item.fill_source_trace))
                remaining_cash = result.cash; remaining_positions = dict(result.positions); remaining_lots = result.inventory_lots
            if per_order and retry_reason in {"INSUFFICIENT_CASH", "FEE_SCHEDULE_CHANGED", "FEE_SCHEDULE_UNAVAILABLE"}:
                break
        # Every GTC order produces exactly one summary audit, including orders
        # that never received an accepted fill.  Attempt details remain nested in
        # retry_trace rather than inflating order-level audit/metrics counts.
        audit = last_result.audits[-1] if last_result is not None and last_result.audits else OrderAudit(
            order_id=intent.order_id or f"order-{len(all_audits) + 1}", symbol=intent.symbol,
            side=intent.side, requested_quantity=float(intent.quantity), status="unfilled",
            reason=terminal_failure_reason or "ORDER_EXPIRED", submitted_at=intent.submitted_at,
            eligible_at=None, fill_at=None)
        complete = filled >= float(intent.quantity) - 1e-9
        summary_reason = "FILLED" if complete else (terminal_failure_reason if terminal_failure_reason not in {None, "NO_LIQUIDITY", "LIMIT_NOT_REACHED", "STOP_NOT_TRIGGERED", "HALTED", "LIMIT_UP", "LIMIT_DOWN"} else "ORDER_EXPIRED")
        summary_status = "filled" if complete else ("partial" if filled > 0 else "unfilled")
        fee_metadata = dict(audit.query_metadata)
        if per_order:
            # A rejected attempt must not make the summary advertise its
            # PER_FILL estimate as an order-level charge.
            for key in ("fee_schedule_ref", "fee_schedule_version", "fee_schedule_identity",
                        "fee_schedule_hash", "fee_breakdown", "aggregate_fee_breakdown"):
                fee_metadata.pop(key, None)
            fee_metadata["fee_mode"] = "PER_ORDER"
            if order_accumulator is None:
                # No accepted fill means no resolved/charged order-level fee.
                fee_metadata["fee_source"] = "none"
                audit = replace(audit, fee_mode="PER_ORDER", fee_source="none",
                                fee_schedule_ref=None, fee_schedule_version=None,
                                fee_schedule_identity=None, fee_schedule_hash=None,
                                fee_breakdown={})
            else:
                aggregate_breakdown = order_accumulator.aggregate_breakdown()
                fee_metadata.update({"fee_source": "fee_schedule",
                                     "fee_schedule_ref": order_accumulator.schedule.schedule_ref,
                                     "fee_schedule_version": order_accumulator.schedule.version,
                                     "fee_schedule_identity": order_accumulator.schedule_identity,
                                     "fee_schedule_hash": order_accumulator.schedule_hash,
                                     "fee_breakdown": _json_value(aggregate_breakdown.as_dict())})
                audit = replace(audit, fee_mode="PER_ORDER", fee_source="fee_schedule",
                                fee_schedule_ref=order_accumulator.schedule.schedule_ref,
                                fee_schedule_version=order_accumulator.schedule.version,
                                fee_schedule_identity=order_accumulator.schedule_identity,
                                fee_schedule_hash=order_accumulator.schedule_hash,
                                fee_breakdown=_json_value(aggregate_breakdown.as_dict()))
        all_audits.append(replace(audit, requested_quantity=float(intent.quantity),
                                  fill_at=state.last_fill_at if filled > 0 else None,
                                  filled_quantity=filled,
                                  remaining_quantity=max(0.0, float(intent.quantity) - filled),
                                  status=summary_status, reason=summary_reason,
                                  fill_reason=summary_reason,
                                  source_trace=state.source_trace,
                                  fill_source_trace=state.source_trace,
                                  fill_count=len([item for item in all_fills if item.order_id == audit.order_id]),
                                  query_metadata={**fee_metadata, "retry_trace": _json_value(retry_trace),
                                                  "time_in_force": intent.time_in_force,
                                                  "attempts": state.attempts,
                                                  "terminal_reason": "FILLED" if complete else (summary_reason or "ORDER_EXPIRED"),
                                                  "retry_reasons": [item["reason"] for item in retry_trace],
                                                  "fill_count": state.fill_count,
                                                  "source_trace": list(state.source_trace),
                                                  **({"fee_schedule_identity": order_accumulator.schedule_identity,
                                                      "fee_schedule_hash": order_accumulator.schedule_hash,
                                                      "aggregate_fee_breakdown": _json_value(order_accumulator.aggregate_breakdown().as_dict())}
                                                     if order_accumulator is not None and order_accumulator.aggregate_breakdown() is not None else {})}))
    filled_total = sum(float(item.quantity) for item in all_fills)
    partial_filled_qty = sum(float(audit.filled_quantity) for audit in all_audits if audit.status == "partial")
    remaining_total = sum(float(audit.remaining_quantity or 0.0) for audit in all_audits)
    unfilled_total = sum(1 for audit in all_audits if audit.status != "filled")
    metrics = {"submitted": len(ordered), "filled": len(all_fills),
               "unfilled": unfilled_total,
               "unfilled_rate": unfilled_total / len(ordered) if ordered else 0.0,
               "partial_count": sum(1 for audit in all_audits if audit.status == "partial"),
               "fully_filled_count": sum(1 for audit in all_audits if audit.status == "filled"),
               "filled_quantity": filled_total, "partial_filled_qty": partial_filled_qty,
               "remaining_quantity": remaining_total}
    equity, inventory_positions = _value_inventory(
        store, query, float(remaining_cash), tuple(remaining_lots), policy.currency)
    return ExecutionResult(tuple(all_fills), tuple(all_audits), remaining_cash, remaining_positions,
                           equity, store.explain(), {}, ordered, metrics, SCHEMA,
                           tuple(remaining_lots), inventory_positions)
