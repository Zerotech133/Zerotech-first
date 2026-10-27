# -*- coding: utf-8 -*-
"""Auditable, Decimal-based trading cost schedules.

This module is intentionally independent from the legacy execution engine.  It
provides an explicit fee contract that callers may adopt without changing the
existing ``fee_bps``/``fee_rate`` behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional, Tuple, Union

Number = Union[str, int, float, Decimal]
DateLike = Union[date, datetime]


class FeeScheduleError(ValueError):
    """Raised when a fee schedule is invalid, missing, or ambiguous."""


def _decimal(value: Number, name: str) -> Decimal:
    """Convert a number without inheriting binary float artefacts."""
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FeeScheduleError(f"{name} must be a valid decimal") from exc
    if not result.is_finite():
        raise FeeScheduleError(f"{name} must be finite")
    return result


def _timestamp(
    value: Optional[DateLike], name: str, *, default: Optional[datetime] = None
) -> Optional[datetime]:
    if value is None:
        return default
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise FeeScheduleError(f"{name} must be date, datetime, or None")


def _identity(value: str, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise FeeScheduleError(f"{name} is required")
    return result


@dataclass(frozen=True)
class FeeSchedule:
    """Immutable rates and applicability interval for one market contract.

    Rates are decimal fractions (``0.0003`` means 3 basis points).  All fee
    components are rounded independently to ``rounding`` before the total is
    calculated.  ``effective_until`` is exclusive, making intervals composable
    without an overlap at midnight or at a version boundary.
    """

    schedule_ref: str
    version: str
    market: str
    exchange: str
    asset_class: str = "equity"
    currency: str = "CNY"
    effective_from: Optional[DateLike] = None
    effective_until: Optional[DateLike] = None
    commission_rate: Number = Decimal("0")
    commission_min: Number = Decimal("0")
    stamp_duty_rate: Number = Decimal("0")
    stamp_duty_side_mask: Tuple[str, ...] = ("SELL",)
    transfer_fee_rate: Number = Decimal("0")
    regulatory_fee_rate: Number = Decimal("0")
    exchange_fee_rate: Number = Decimal("0")
    fixed_fee: Number = Decimal("0")
    rounding: Number = Decimal("0.01")
    rounding_mode: str = "ROUND_HALF_UP"

    def __post_init__(self) -> None:
        for name in (
            "schedule_ref",
            "version",
            "market",
            "exchange",
            "asset_class",
            "currency",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        start = _timestamp(self.effective_from, "effective_from")
        end = _timestamp(self.effective_until, "effective_until")
        if start is not None and end is not None and start >= end:
            raise FeeScheduleError("effective_from must precede effective_until")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_until", end)
        nonnegative = (
            "commission_rate",
            "commission_min",
            "stamp_duty_rate",
            "transfer_fee_rate",
            "regulatory_fee_rate",
            "exchange_fee_rate",
            "fixed_fee",
        )
        for name in nonnegative:
            value = _decimal(getattr(self, name), name)
            if value < 0:
                raise FeeScheduleError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        increment = _decimal(self.rounding, "rounding")
        if increment <= 0:
            raise FeeScheduleError("rounding must be positive")
        object.__setattr__(self, "rounding", increment)
        mode = str(self.rounding_mode).upper()
        modes = {"ROUND_HALF_UP", "ROUND_UP", "ROUND_DOWN"}
        if mode not in modes:
            raise FeeScheduleError(f"rounding_mode must be one of {sorted(modes)}")
        object.__setattr__(self, "rounding_mode", mode)
        mask = tuple(str(side).upper() for side in (self.stamp_duty_side_mask or ()))
        if any(side not in {"BUY", "SELL"} for side in mask):
            raise FeeScheduleError("stamp_duty_side_mask may only contain BUY or SELL")
        object.__setattr__(self, "stamp_duty_side_mask", tuple(sorted(set(mask))))

    @property
    def identity(self) -> str:
        """Stable human-readable identity for this schedule version."""
        return f"{self.schedule_ref}@{self.version}"

    def canonical_payload(self) -> Mapping[str, Any]:
        """Return a deterministic, JSON-safe representation of all schedule fields."""

        def encode(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat()
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, Mapping):
                return {
                    str(key): encode(item)
                    for key, item in sorted(
                        value.items(), key=lambda item: str(item[0])
                    )
                }
            return value

        return {
            "schedule_ref": self.schedule_ref,
            "version": self.version,
            "identity": self.identity,
            "market": self.market,
            "exchange": self.exchange,
            "asset_class": self.asset_class,
            "currency": self.currency,
            "effective_from": encode(self.effective_from),
            "effective_until": encode(self.effective_until),
            "commission_rate": encode(self.commission_rate),
            "commission_min": encode(self.commission_min),
            "stamp_duty_rate": encode(self.stamp_duty_rate),
            "stamp_duty_side_mask": encode(self.stamp_duty_side_mask),
            "transfer_fee_rate": encode(self.transfer_fee_rate),
            "regulatory_fee_rate": encode(self.regulatory_fee_rate),
            "exchange_fee_rate": encode(self.exchange_fee_rate),
            "fixed_fee": encode(self.fixed_fee),
            "rounding": encode(self.rounding),
            "rounding_mode": self.rounding_mode,
        }

    @property
    def schedule_hash(self) -> str:
        """Return SHA-256 of the canonical schedule JSON payload."""
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def applies_at(self, at: DateLike) -> bool:
        """Return whether ``at`` falls in this schedule's half-open interval."""
        moment = _timestamp(at, "at")
        assert moment is not None
        return (self.effective_from is None or moment >= self.effective_from) and (
            self.effective_until is None or moment < self.effective_until
        )

    def overlaps(self, other: "FeeSchedule") -> bool:
        """Return whether two schedules overlap for the same identity."""
        if (self.market, self.exchange, self.asset_class, self.currency) != (
            other.market,
            other.exchange,
            other.asset_class,
            other.currency,
        ):
            return False
        start_a = self.effective_from or datetime.min.replace(tzinfo=timezone.utc)
        end_a = self.effective_until or datetime.max.replace(tzinfo=timezone.utc)
        start_b = other.effective_from or datetime.min.replace(tzinfo=timezone.utc)
        end_b = other.effective_until or datetime.max.replace(tzinfo=timezone.utc)
        return start_a < end_b and start_b < end_a


@dataclass(frozen=True)
class CostBreakdown:
    """A complete, cash-flow auditable cost result."""

    schedule_ref: str
    version: str
    market: str
    exchange: str
    asset_class: str
    currency: str
    side: str
    quantity: Decimal
    price: Decimal
    gross_value: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    regulatory_fee: Decimal
    exchange_fee: Decimal
    fixed_fee: Decimal
    total: Decimal
    cash_delta: Decimal
    # Unrounded component values are appended for auditability.  ``None`` keeps
    # legacy positional construction source-compatible and defaults to rounded
    # values when callers construct a breakdown manually.
    raw_commission: Optional[Decimal] = None
    raw_stamp_duty: Optional[Decimal] = None
    raw_transfer_fee: Optional[Decimal] = None
    raw_regulatory_fee: Optional[Decimal] = None
    raw_exchange_fee: Optional[Decimal] = None
    raw_fixed_fee: Optional[Decimal] = None
    # Stable SHA-256 identity of the resolved schedule.  Appended after all
    # existing optional fields to preserve legacy positional construction.
    schedule_hash: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate signed cash semantics and fee-component conservation."""
        side = str(self.side).upper()
        if side not in {"BUY", "SELL"}:
            raise FeeScheduleError("CostBreakdown side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        decimal_fields = (
            "quantity",
            "price",
            "gross_value",
            "commission",
            "stamp_duty",
            "transfer_fee",
            "regulatory_fee",
            "exchange_fee",
            "fixed_fee",
            "total",
            "cash_delta",
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise FeeScheduleError(f"CostBreakdown {name} must be a finite Decimal")
        raw_fields = (
            "raw_commission",
            "raw_stamp_duty",
            "raw_transfer_fee",
            "raw_regulatory_fee",
            "raw_exchange_fee",
            "raw_fixed_fee",
        )
        rounded_components = (
            self.commission,
            self.stamp_duty,
            self.transfer_fee,
            self.regulatory_fee,
            self.exchange_fee,
            self.fixed_fee,
        )
        for name, rounded in zip(raw_fields, rounded_components):
            value = getattr(self, name)
            if value is None:
                object.__setattr__(self, name, rounded)
            elif not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise FeeScheduleError(
                    f"CostBreakdown {name} must be a finite non-negative Decimal"
                )
        if self.quantity <= 0 or self.price <= 0 or self.gross_value < 0:
            raise FeeScheduleError("CostBreakdown quantity and price must be positive")
        components = (
            self.commission,
            self.stamp_duty,
            self.transfer_fee,
            self.regulatory_fee,
            self.exchange_fee,
            self.fixed_fee,
        )
        if any(component < 0 for component in components) or self.total < 0:
            raise FeeScheduleError("CostBreakdown fees must be non-negative")
        if self.total != sum(components, Decimal("0")):
            raise FeeScheduleError("CostBreakdown total does not equal fee components")
        expected = (
            -(self.gross_value + self.total)
            if side == "BUY"
            else self.gross_value - self.total
        )
        if self.cash_delta != expected:
            raise FeeScheduleError(
                "CostBreakdown cash_delta is inconsistent with side and total"
            )

    def as_dict(self) -> Mapping[str, Any]:
        """Return a JSON-safe Decimal-string representation for audit storage."""

        def encode(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat()
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, Mapping):
                return {str(key): encode(item) for key, item in value.items()}
            return value

        return {name: encode(getattr(self, name)) for name in self.__dataclass_fields__}

    def to_json(self) -> str:
        """Serialize this breakdown deterministically without float coercion."""
        return json.dumps(
            self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


class FeeScheduleProvider:
    """Fail-closed registry for immutable fee schedules."""

    def __init__(self, schedules: Iterable[FeeSchedule] = ()) -> None:
        self._schedules: list[FeeSchedule] = []
        for schedule in schedules:
            self.add(schedule)

    @property
    def schedules(self) -> Tuple[FeeSchedule, ...]:
        """Return registered schedules in insertion order."""
        return tuple(self._schedules)

    def add(self, schedule: FeeSchedule) -> None:
        """Register a schedule, rejecting overlapping identity intervals."""
        if not isinstance(schedule, FeeSchedule):
            raise FeeScheduleError("schedule must be a FeeSchedule")
        if any(schedule.overlaps(existing) for existing in self._schedules):
            raise FeeScheduleError("overlapping fee schedule interval")
        self._schedules.append(schedule)

    register = add

    def resolve(
        self,
        *,
        market: str,
        exchange: str,
        asset_class: str = "equity",
        currency: str = "CNY",
        at: DateLike,
    ) -> FeeSchedule:
        """Resolve exactly one applicable schedule or fail closed."""
        candidates = [
            s
            for s in self._schedules
            if s.market == str(market).strip()
            and s.exchange == str(exchange).strip()
            and s.asset_class == str(asset_class).strip()
            and s.currency == str(currency).strip()
            and s.applies_at(at)
        ]
        if len(candidates) != 1:
            reason = "missing" if not candidates else "ambiguous"
            raise FeeScheduleError(f"fee schedule {reason} for requested identity/time")
        return candidates[0]

    get = resolve


class CostCalculator:
    """Calculate rounded fees and signed cash deltas using a provider."""

    def __init__(self, provider: FeeScheduleProvider) -> None:
        if not isinstance(provider, FeeScheduleProvider):
            raise FeeScheduleError("provider must be a FeeScheduleProvider")
        self.provider = provider

    @staticmethod
    def _round(value: Decimal, schedule: FeeSchedule) -> Decimal:
        quantum = schedule.rounding
        return value.quantize(
            quantum, rounding=getattr(__import__("decimal"), schedule.rounding_mode)
        )

    def calculate(
        self,
        *,
        side: str,
        quantity: Number,
        price: Number,
        at: DateLike,
        market: str,
        exchange: str,
        asset_class: str = "equity",
        currency: str = "CNY",
    ) -> CostBreakdown:
        """Calculate all applicable fees for a filled quantity and price."""
        normalized_side = str(side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise FeeScheduleError("side must be BUY or SELL")
        qty = _decimal(quantity, "quantity")
        unit_price = _decimal(price, "price")
        if qty <= 0 or unit_price <= 0:
            raise FeeScheduleError("quantity and price must be positive")
        schedule = self.provider.resolve(
            market=market,
            exchange=exchange,
            asset_class=asset_class,
            currency=currency,
            at=at,
        )
        gross = qty * unit_price
        commission = max(gross * schedule.commission_rate, schedule.commission_min)
        stamp = (
            gross * schedule.stamp_duty_rate
            if normalized_side in schedule.stamp_duty_side_mask
            else Decimal("0")
        )
        transfer = gross * schedule.transfer_fee_rate
        regulatory = gross * schedule.regulatory_fee_rate
        exchange_fee = gross * schedule.exchange_fee_rate
        fixed = schedule.fixed_fee
        raw_values = (commission, stamp, transfer, regulatory, exchange_fee, fixed)
        values = [self._round(value, schedule) for value in raw_values]
        commission, stamp, transfer, regulatory, exchange_fee, fixed = values
        total = sum(values, Decimal("0"))
        cash_delta = -(gross + total) if normalized_side == "BUY" else gross - total
        return CostBreakdown(
            schedule.schedule_ref,
            schedule.version,
            schedule.market,
            schedule.exchange,
            schedule.asset_class,
            schedule.currency,
            normalized_side,
            qty,
            unit_price,
            gross,
            commission,
            stamp,
            transfer,
            regulatory,
            exchange_fee,
            fixed,
            total,
            cash_delta,
            *raw_values,
            schedule.schedule_hash,
        )


@dataclass(frozen=True)
class FeeAllocation:
    """One fill's allocated share of an order-level fee.

    Allocation amounts are calculated from cumulative rounded component totals,
    rather than by independently rounding each fill.  Consequently, adding all
    component amounts across an order is exactly equivalent to calculating the
    order as one fill.
    """

    schedule_ref: str
    version: str
    market: str
    exchange: str
    asset_class: str
    currency: str
    side: str
    quantity: Decimal
    price: Decimal
    gross_value: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    regulatory_fee: Decimal
    exchange_fee: Decimal
    fixed_fee: Decimal
    total: Decimal
    cash_delta: Decimal
    raw_commission: Decimal
    raw_stamp_duty: Decimal
    raw_transfer_fee: Decimal
    raw_regulatory_fee: Decimal
    raw_exchange_fee: Decimal
    raw_fixed_fee: Decimal
    schedule_hash: str

    def __post_init__(self) -> None:
        """Validate allocation conservation and schedule identity."""
        normalized_side = str(self.side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise FeeScheduleError("FeeAllocation side must be BUY or SELL")
        object.__setattr__(self, "side", normalized_side)
        decimal_fields = (
            "quantity",
            "price",
            "gross_value",
            "commission",
            "stamp_duty",
            "transfer_fee",
            "regulatory_fee",
            "exchange_fee",
            "fixed_fee",
            "total",
            "cash_delta",
            "raw_commission",
            "raw_stamp_duty",
            "raw_transfer_fee",
            "raw_regulatory_fee",
            "raw_exchange_fee",
            "raw_fixed_fee",
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise FeeScheduleError(f"FeeAllocation {name} must be a finite Decimal")
            if (
                name
                in {
                    "commission",
                    "stamp_duty",
                    "transfer_fee",
                    "regulatory_fee",
                    "exchange_fee",
                    "fixed_fee",
                    "total",
                    "raw_commission",
                    "raw_stamp_duty",
                    "raw_transfer_fee",
                    "raw_regulatory_fee",
                    "raw_exchange_fee",
                    "raw_fixed_fee",
                }
                and value < 0
            ):
                raise FeeScheduleError(f"FeeAllocation {name} must be non-negative")
        if self.quantity <= 0 or self.price <= 0 or self.gross_value < 0:
            raise FeeScheduleError("FeeAllocation quantity and price must be positive")
        components = (
            self.commission,
            self.stamp_duty,
            self.transfer_fee,
            self.regulatory_fee,
            self.exchange_fee,
            self.fixed_fee,
        )
        if self.total != sum(components, Decimal("0")):
            raise FeeScheduleError("FeeAllocation total does not equal fee components")
        expected_cash = (
            -(self.gross_value + self.total)
            if normalized_side == "BUY"
            else self.gross_value - self.total
        )
        if self.cash_delta != expected_cash:
            raise FeeScheduleError(
                "FeeAllocation cash_delta is inconsistent with side and total"
            )
        if not str(self.schedule_hash).strip():
            raise FeeScheduleError("FeeAllocation schedule_hash is required")

    @property
    def fee_total(self) -> Decimal:
        """Return the total allocated fee (alias useful to audit consumers)."""
        return self.total

    @property
    def gross(self) -> Decimal:
        """Return gross traded value."""
        return self.gross_value

    def as_dict(self) -> Mapping[str, Any]:
        """Return a JSON-safe representation with Decimal values as strings."""
        return {
            name: str(value) if isinstance(value, Decimal) else value
            for name, value in (
                (name, getattr(self, name)) for name in self.__dataclass_fields__
            )
        }


class OrderFeeAccumulator:
    """Accumulate fills while charging order-level fees exactly once.

    The schedule is snapshotted by both identity and SHA-256 hash at
    construction.  Every fill may optionally provide the schedule (or hash)
    it used; a mismatch raises :class:`FeeScheduleError` instead of silently
    mixing fee versions.  ``allocations`` is recomputed from all fills after
    each append, so its values always conserve the current aggregate total.
    """

    def __init__(self, schedule: FeeSchedule, side: Optional[str] = None) -> None:
        if not isinstance(schedule, FeeSchedule):
            raise FeeScheduleError("schedule must be a FeeSchedule")
        normalized_side = None if side is None else str(side).upper()
        if normalized_side is not None and normalized_side not in {"BUY", "SELL"}:
            raise FeeScheduleError("side must be BUY or SELL")
        self._schedule = schedule
        self._schedule_identity = schedule.identity
        self._schedule_hash = schedule.schedule_hash
        self._side = normalized_side
        self._fills: list[Tuple[Decimal, Decimal]] = []
        self._allocations: Tuple[FeeAllocation, ...] = ()

    @property
    def schedule(self) -> FeeSchedule:
        """Return the immutable schedule captured by this accumulator."""
        return self._schedule

    @property
    def schedule_identity(self) -> str:
        """Return the captured ``schedule_ref@version`` identity."""
        return self._schedule_identity

    @property
    def schedule_hash(self) -> str:
        """Return the captured schedule hash."""
        return self._schedule_hash

    @property
    def side(self) -> Optional[str]:
        """Return the order side, if known."""
        return self._side

    @property
    def fills(self) -> Tuple[Tuple[Decimal, Decimal], ...]:
        """Return immutable ``(quantity, price)`` fill inputs."""
        return tuple(self._fills)

    @property
    def allocations(self) -> Tuple[FeeAllocation, ...]:
        """Return current per-fill allocations in insertion order."""
        return self._allocations

    @property
    def quantity(self) -> Decimal:
        """Return cumulative filled quantity."""
        return sum((quantity for quantity, _ in self._fills), Decimal("0"))

    @property
    def gross_value(self) -> Decimal:
        """Return cumulative gross traded value."""
        return sum((quantity * price for quantity, price in self._fills), Decimal("0"))

    @property
    def total(self) -> Decimal:
        """Return the aggregate allocated fee."""
        return sum((allocation.total for allocation in self._allocations), Decimal("0"))

    @staticmethod
    def _round_value(value: Decimal, schedule: FeeSchedule) -> Decimal:
        quantum = schedule.rounding
        return value.quantize(
            quantum, rounding=getattr(__import__("decimal"), schedule.rounding_mode)
        )

    def _check_schedule(
        self, schedule: Optional[FeeSchedule], schedule_hash: Optional[str]
    ) -> None:
        if schedule is not None:
            if not isinstance(schedule, FeeSchedule):
                raise FeeScheduleError("schedule must be a FeeSchedule")
            if (
                schedule.identity != self._schedule_identity
                or schedule.schedule_hash != self._schedule_hash
            ):
                raise FeeScheduleError("fee schedule identity/hash mismatch")
        if schedule_hash is not None and str(schedule_hash) != self._schedule_hash:
            raise FeeScheduleError("fee schedule hash mismatch")

    def add_fill(
        self,
        quantity: Number,
        price: Number,
        *,
        side: Optional[str] = None,
        schedule: Optional[FeeSchedule] = None,
        schedule_hash: Optional[str] = None,
    ) -> FeeAllocation:
        """Append one fill and return its current allocation.

        Existing allocations may be revised when a later fill changes the
        aggregate commission minimum or rounded total.  Consumers should read
        ``allocations`` (or call ``finalize``) after all fills are appended.
        """
        self._check_schedule(schedule, schedule_hash)
        normalized_side = self._side if side is None else str(side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise FeeScheduleError("side must be BUY or SELL")
        if self._side is not None and normalized_side != self._side:
            raise FeeScheduleError("order side mismatch")
        qty = _decimal(quantity, "quantity")
        unit_price = _decimal(price, "price")
        if qty <= 0 or unit_price <= 0:
            raise FeeScheduleError("quantity and price must be positive")
        self._side = normalized_side
        self._fills.append((qty, unit_price))
        self._recompute_allocations()
        return self._allocations[-1]

    append_fill = add_fill

    def _component_raw(self, gross: Decimal, side: str) -> Tuple[Decimal, ...]:
        schedule = self._schedule
        commission = max(gross * schedule.commission_rate, schedule.commission_min)
        stamp = (
            gross * schedule.stamp_duty_rate
            if side in schedule.stamp_duty_side_mask
            else Decimal("0")
        )
        return (
            commission,
            stamp,
            gross * schedule.transfer_fee_rate,
            gross * schedule.regulatory_fee_rate,
            gross * schedule.exchange_fee_rate,
            schedule.fixed_fee,
        )

    def _recompute_allocations(self) -> None:
        if not self._fills or self._side is None:
            self._allocations = ()
            return
        schedule = self._schedule
        previous_raw = (Decimal("0"),) * 6
        previous_rounded = (Decimal("0"),) * 6
        cumulative_gross = Decimal("0")
        result: list[FeeAllocation] = []
        for qty, price in self._fills:
            gross = qty * price
            cumulative_gross += gross
            cumulative_raw = self._component_raw(cumulative_gross, self._side)
            cumulative_rounded = tuple(
                self._round_value(value, schedule) for value in cumulative_raw
            )
            raw_delta = tuple(
                current - prior for current, prior in zip(cumulative_raw, previous_raw)
            )
            rounded_delta = tuple(
                current - prior
                for current, prior in zip(cumulative_rounded, previous_rounded)
            )
            total = sum(rounded_delta, Decimal("0"))
            cash_delta = -(gross + total) if self._side == "BUY" else gross - total
            result.append(
                FeeAllocation(
                    schedule.schedule_ref,
                    schedule.version,
                    schedule.market,
                    schedule.exchange,
                    schedule.asset_class,
                    schedule.currency,
                    self._side,
                    qty,
                    price,
                    gross,
                    *rounded_delta,
                    total,
                    cash_delta,
                    *raw_delta,
                    self._schedule_hash,
                )
            )
            previous_raw = cumulative_raw
            previous_rounded = cumulative_rounded
        self._allocations = tuple(result)

    def finalize(self) -> Tuple[FeeAllocation, ...]:
        """Return immutable allocations; empty orders return no fees."""
        self._recompute_allocations()
        return self._allocations

    def aggregate_breakdown(self) -> Optional[CostBreakdown]:
        """Return a CostBreakdown for the whole order, or ``None`` if empty."""
        if not self._allocations or self._side is None:
            return None
        qty = self.quantity
        gross = self.gross_value
        weighted_price = gross / qty
        components = tuple(
            sum((getattr(item, field) for item in self._allocations), Decimal("0"))
            for field in (
                "commission",
                "stamp_duty",
                "transfer_fee",
                "regulatory_fee",
                "exchange_fee",
                "fixed_fee",
            )
        )
        raw_components = tuple(
            sum(
                (getattr(item, "raw_" + field) for item in self._allocations),
                Decimal("0"),
            )
            for field in (
                "commission",
                "stamp_duty",
                "transfer_fee",
                "regulatory_fee",
                "exchange_fee",
                "fixed_fee",
            )
        )
        total = sum(components, Decimal("0"))
        cash_delta = -(gross + total) if self._side == "BUY" else gross - total
        return CostBreakdown(
            self._schedule.schedule_ref,
            self._schedule.version,
            self._schedule.market,
            self._schedule.exchange,
            self._schedule.asset_class,
            self._schedule.currency,
            self._side,
            qty,
            weighted_price,
            gross,
            *components,
            total,
            cash_delta,
            *raw_components,
            self._schedule_hash,
        )

    @property
    def aggregate(self) -> Optional[CostBreakdown]:
        """Return the aggregate CostBreakdown (alias for ``aggregate_breakdown``)."""
        return self.aggregate_breakdown()


@dataclass(frozen=True)
class LegacyFlatFeeSchedule:
    """Explicit adapter for the old flat fee convention.

    ``fee_bps`` is the legacy execution-engine unit.  ``fee_rate`` remains
    available for callers that already supplied a decimal fraction.  This
    adapter must be used instead of applying both a schedule and legacy fee.
    """

    fee_rate: Optional[Number] = None
    fee_bps: Optional[Number] = None
    schedule_ref: str = "legacy-flat"
    version: str = "1"
    market: str = "legacy"
    exchange: str = "legacy"
    asset_class: str = "equity"
    currency: str = "CNY"
    # Preserve the legacy engine's unrounded fractional fee by default.
    rounding: Number = Decimal("0.0000000001")

    def __post_init__(self) -> None:
        if self.fee_rate is None and self.fee_bps is None:
            object.__setattr__(self, "fee_rate", Decimal("0"))
        if self.fee_rate is not None and self.fee_bps is not None:
            expected = _decimal(self.fee_bps, "fee_bps") / Decimal("10000")
            supplied = _decimal(self.fee_rate, "fee_rate")
            if supplied != expected:
                raise FeeScheduleError("fee_rate and fee_bps disagree")
        if self.fee_bps is not None and _decimal(self.fee_bps, "fee_bps") < 0:
            raise FeeScheduleError("fee_bps must be non-negative")

    def to_schedule(self) -> FeeSchedule:
        """Convert to a normal schedule; callers must not apply legacy fees again."""
        rate = (
            _decimal(self.fee_bps, "fee_bps") / Decimal("10000")
            if self.fee_bps is not None
            else _decimal(self.fee_rate or 0, "fee_rate")
        )
        return FeeSchedule(
            schedule_ref=self.schedule_ref,
            version=self.version,
            market=self.market,
            exchange=self.exchange,
            asset_class=self.asset_class,
            currency=self.currency,
            commission_rate=rate,
            stamp_duty_side_mask=(),
            rounding=self.rounding,
        )


# The design documents use both names; keep the public vocabulary compatible.
FeeBreakdown = CostBreakdown
