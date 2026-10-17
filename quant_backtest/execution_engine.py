# -*- coding: utf-8 -*-
"""Deterministic, read-only minute execution over canonical :class:`BarStore` data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import math
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from bar_dto import Bar, BarQuery, ensure_utc
from bar_store import BarStore
from market_rules import MarketRuleProvider

Provider = Callable[..., Any]
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
            self.gap_stop_pct < 0 or not math.isfinite(float(self.gap_stop_pct))
        ):
            raise ValueError("gap_stop_pct must be finite and non-negative")
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
    normalized_symbol = str(symbol).upper()
    normalized_market = str(market or "UNKNOWN").upper()
    if exchange:
        normalized_exchange = str(exchange).upper()
    elif normalized_market == "CN" and normalized_symbol.endswith(".SH"):
        normalized_exchange = "SSE"
    elif normalized_market == "CN" and normalized_symbol.endswith(".SZ"):
        normalized_exchange = "SZSE"
    else:
        normalized_exchange = "UNKNOWN"
    return normalized_market, normalized_exchange, str(asset_class or "equity").lower(), normalized_symbol


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
    inventory = []
    explicit_inventory = bool(policy.initial_lots)
    bought_lots: Dict[Tuple[str, date], float] = {}
    lot_counter = 0
    default_market = (policy.market or (policy.policy_ref.rsplit("-", 1)[-1] if policy.policy_ref else "UNKNOWN")).upper()
    for symbol, quantity in policy.initial_positions.items():
        if float(quantity) > 0:
            lot_counter += 1
            market, exchange, asset_class, _ = _instrument_identity(str(symbol), default_market)
            inventory.append(InventoryLot(str(symbol), market, exchange, asset_class, None, None, float(quantity), f"initial-{lot_counter}"))
    for lot in policy.initial_lots:
        inventory.append(lot)
        positions[lot.symbol] = positions.get(lot.symbol, 0.0) + float(lot.quantity)
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
        raw_price = float(eligible.o)
        if intent.order_type == "STOP":
            if intent.side != "SELL" or intent.stop_price is None:
                audits.append(OrderAudit(status="unfilled", reason="INVALID_STOP", eligible_at=eligible.dt, fill_at=None, **common)); continue
            if eligible.o > intent.stop_price and (eligible.l is None or eligible.l > intent.stop_price):
                audits.append(OrderAudit(status="unfilled", reason="STOP_NOT_TRIGGERED", eligible_at=eligible.dt, fill_at=None, **common)); continue
            raw_price = float(eligible.o) if eligible.o <= intent.stop_price else float(intent.stop_price)
        if market_rule_provider is not None or policy.market_rules_required:
            rule_provider = market_rule_provider or MarketRuleProvider(
                status_provider=status_provider, limit_provider=limit_provider,
                market=intent.market or policy.market or getattr(eligible, "market", "")
            )
            rule_decision = rule_provider.decide(intent.symbol, eligible.dt, eligible, intent.side, raw_price)
            common["source_trace"] = tuple(dict.fromkeys(tuple(common["source_trace"]) + tuple(rule_decision.source_trace)))
            common["query_metadata"] = {**query_metadata, "rule_ref": rule_decision.rule_ref,
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
        quantity = _quantity(intent.quantity, policy.lot_size)
        if quantity <= 0:
            audits.append(OrderAudit(status="unfilled", reason="BELOW_LOT", eligible_at=eligible.dt, fill_at=None, **common)); continue
        if intent.side == "SELL":
            sell_identity = _instrument_identity(
                intent.symbol, intent.market or policy.market or default_market,
                intent.exchange, intent.asset_class
            )
            matching = [lot for lot in inventory if _instrument_identity(
                lot.symbol, lot.market, lot.exchange, lot.asset_class
            ) == sell_identity]
            total_inventory = sum(float(lot.quantity) for lot in matching)
            if not matching and not explicit_inventory and intent.symbol in positions and positions[intent.symbol] > 0:
                total_inventory = float(positions[intent.symbol])
            if quantity > total_inventory + 1e-9:
                audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_POSITION", eligible_at=eligible.dt, fill_at=None, **common)); continue
            if policy.t_plus_one:
                known_available = sum(float(lot.quantity) for lot in matching
                                      if lot.available_session is not None and lot.available_session <= eligible.session_date)
                has_unknown_age = any(lot.available_session is None for lot in matching)
                if not matching and not explicit_inventory and intent.symbol in positions and positions[intent.symbol] > 0:
                    known_available = float(positions[intent.symbol])
                if quantity > known_available + 1e-9:
                    audits.append(OrderAudit(status="unfilled", reason="INVENTORY_AGE_UNKNOWN" if has_unknown_age and matching else "T_PLUS_ONE", eligible_at=eligible.dt, fill_at=None, **common)); continue
        direction = 1.0 if intent.side == "BUY" else -1.0
        price = raw_price * (1.0 + direction * float(policy.slippage_bps) / 10000.0)
        gross = price * quantity
        fee = gross * float(policy.fee_bps) / 10000.0
        total = gross + fee if intent.side == "BUY" else gross - fee
        if intent.side == "BUY" and total > cash + 1e-9:
            audits.append(OrderAudit(status="unfilled", reason="INSUFFICIENT_CASH", eligible_at=eligible.dt, fill_at=None, **common)); continue
        cash += -total if intent.side == "BUY" else total
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
            bought_lots[(intent.symbol, eligible.session_date)] = bought_lots.get((intent.symbol, eligible.session_date), 0.0) + quantity
        fills.append(Fill(order_id, intent.symbol, intent.side, quantity, price, gross, fee,
                          abs(price - raw_price) * quantity, eligible.dt, eligible.source))
        audits.append(OrderAudit(status="filled", reason="FILLED", eligible_at=eligible.dt, fill_at=eligible.dt, **common))
    mark: Dict[Tuple[str, str, str, str], float] = {}
    for bar in bars:
        if policy.market and bar.market.upper() != policy.market.upper():
            continue
        if policy.timeframe and bar.timeframe.lower() != policy.timeframe.lower():
            continue
        identity = _instrument_identity(bar.symbol, bar.market)
        mark[identity] = float(bar.c if bar.c is not None else bar.o or 0.0)
    equity = cash
    for lot in inventory:
        identity = _instrument_identity(lot.symbol, lot.market, lot.exchange, lot.asset_class)
        equity += float(lot.quantity) * mark.get(identity, 0.0)
    if not explicit_inventory:
        for symbol, quantity in positions.items():
            identity = _instrument_identity(symbol, default_market)
            if not any(_instrument_identity(lot.symbol, lot.market, lot.exchange, lot.asset_class) == identity for lot in inventory):
                equity += quantity * mark.get(identity, 0.0)
    metrics = {"submitted": len(ordered_intents), "filled": len(fills),
               "unfilled": len(ordered_intents) - len(fills),
               "unfilled_rate": (len(ordered_intents) - len(fills)) / len(ordered_intents) if ordered_intents else 0.0}
    instrument_positions: Dict[Tuple[str, str, str, str], float] = {}
    for lot in inventory:
        key = _instrument_identity(lot.symbol, lot.market, lot.exchange, lot.asset_class)
        instrument_positions[key] = instrument_positions.get(key, 0.0) + float(lot.quantity)
    return ExecutionResult(tuple(fills), tuple(audits), cash, dict(positions), equity,
                           _json_value({**store.explain(), **({"calendar": calendar_identity} if calendar_identity else {})}), query_metadata, ordered_intents, metrics, SCHEMA,
                           tuple(inventory), dict(instrument_positions))
