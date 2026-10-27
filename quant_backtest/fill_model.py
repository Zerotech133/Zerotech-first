# -*- coding: utf-8 -*-
"""Optional, explicit fill-decision contracts.

The legacy execution path does not invoke these models unless a future caller
opts in.  Their DTOs are nevertheless stable so that partial-fill execution
can be added without changing historical constructors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


_CANONICAL_REASONS = {
    "FILLED": "FILLED",
    "PARTIAL": "PARTIAL_FILL",
    "PARTIAL_FILL": "PARTIAL_FILL",
    "PARTIAL_VOLUME": "PARTIAL_FILL",
    "VOLUME_UNAVAILABLE": "NO_LIQUIDITY",
    "BELOW_MINIMUM": "NO_LIQUIDITY",
    "NO_LIQUIDITY": "NO_LIQUIDITY",
}


def _freeze(value: Any) -> Any:
    """Recursively freeze metadata into immutable standard containers."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _canonical_reason(value: str) -> str:
    normalized = str(value).upper().strip()
    return _CANONICAL_REASONS.get(normalized, normalized or "UNKNOWN")


@dataclass(frozen=True, init=False)
class FillDecision:
    """Immutable model result, retaining the historical positional contract.

    The first nine positional arguments remain ``quantity, price, status,
    reason, model_ref, model_version, source_trace, metadata`` (with the
    existing defaults).  New quantity accounting and ``reason_code`` are
    keyword-only additions.  ``reason`` retains the legacy value while
    ``reason_code`` exposes the canonical value.
    """

    quantity: float
    price: Optional[float]
    status: str
    reason: str
    model_ref: Optional[str]
    model_version: Optional[str]
    source_trace: Tuple[str, ...]
    metadata: Mapping[str, Any]
    requested_quantity: Optional[float]
    available_quantity: Optional[float]
    remaining_quantity: Optional[float]
    _canonical_reason: str

    def __init__(
        self,
        quantity: float,
        price: Optional[float] = None,
        status: str = "FILLED",
        reason: str = "FILLED",
        model_ref: Optional[str] = None,
        model_version: Optional[str] = None,
        source_trace: Tuple[str, ...] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        requested_quantity: Optional[float] = None,
        available_quantity: Optional[float] = None,
        remaining_quantity: Optional[float] = None,
        *,
        reason_code: Optional[str] = None,
        available_volume: Optional[float] = None,
    ) -> None:
        if (
            available_quantity is not None
            and available_volume is not None
            and not math.isclose(float(available_quantity), float(available_volume))
        ):
            raise ValueError("available_quantity and available_volume disagree")
        if available_quantity is None:
            available_quantity = available_volume
        normalized_quantity = float(quantity)
        if not math.isfinite(normalized_quantity) or normalized_quantity < 0:
            raise ValueError("FillDecision quantity must be finite and non-negative")
        normalized_price: Optional[float] = None
        if price is not None:
            normalized_price = float(price)
            if not math.isfinite(normalized_price) or normalized_price <= 0:
                raise ValueError("FillDecision price must be finite and positive")
        normalized_status = str(status).upper().strip()
        if normalized_status not in {"FILLED", "PARTIAL", "REJECTED", "UNFILLED"}:
            raise ValueError(
                "FillDecision status must be FILLED, PARTIAL, REJECTED, or UNFILLED"
            )
        legacy_reason = str(reason).upper().strip() or "UNKNOWN"
        canonical = _canonical_reason(
            reason_code if reason_code is not None else legacy_reason
        )
        values = {
            "quantity": normalized_quantity,
            "price": normalized_price,
            "status": normalized_status,
            "reason": legacy_reason,
            "model_ref": (str(model_ref).strip() if model_ref is not None else None),
            "model_version": (
                str(model_version).strip() if model_version is not None else None
            ),
            "source_trace": tuple(str(item) for item in source_trace),
            "metadata": _freeze(dict(metadata or {})),
            "requested_quantity": requested_quantity,
            "available_quantity": available_quantity,
            "remaining_quantity": remaining_quantity,
            "_canonical_reason": canonical,
        }
        for name in ("requested_quantity", "available_quantity", "remaining_quantity"):
            raw = values[name]
            if raw is not None:
                numeric = float(raw)
                if not math.isfinite(numeric) or numeric < 0:
                    raise ValueError(
                        f"FillDecision {name} must be finite and non-negative"
                    )
                values[name] = numeric
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def reason_code(self) -> str:
        """Canonical reason, stable across legacy reason spellings."""
        return self._canonical_reason

    @property
    def filled_quantity(self) -> float:
        """Compatibility alias for the accepted quantity."""
        return self.quantity

    @property
    def fill_price(self) -> Optional[float]:
        """Compatibility alias for the accepted unit price."""
        return self.price

    @property
    def requested(self) -> Optional[float]:
        """Short alias for requested quantity."""
        return self.requested_quantity

    @property
    def available(self) -> Optional[float]:
        """Short alias for available quantity."""
        return self.available_quantity

    @property
    def available_volume(self) -> Optional[float]:
        """Legacy alias for volume available to the order."""
        return self.available_quantity

    @property
    def remaining(self) -> Optional[float]:
        """Short alias for remaining quantity."""
        return self.remaining_quantity


class FillModel(ABC):
    """Explicit, opt-in contract for deciding a requested fill."""

    model_ref: str = "fill-model"
    model_version: str = "1"

    @abstractmethod
    def decide(
        self,
        intent: Any,
        bar: Any,
        requested_quantity: float,
        reference_price: Optional[float] = None,
    ) -> FillDecision:
        """Return a fill decision without mutating execution state."""
        raise NotImplementedError

    def evaluate(
        self,
        intent: Any,
        bar: Any,
        requested_quantity: float,
        reference_price: Optional[float] = None,
    ) -> FillDecision:
        """Positional compatibility entry point for model evaluation."""
        return self.decide(intent, bar, requested_quantity, reference_price)


@dataclass(frozen=True)
class AllOrNoneFillModel(FillModel):
    """Explicit model equivalent to the historical all-or-none fill path."""

    model_ref: str = "legacy_all_or_none_v1"
    model_version: str = "1"

    def decide(
        self,
        intent: Any,
        bar: Any,
        requested_quantity: float,
        reference_price: Optional[float] = None,
    ) -> FillDecision:
        requested = float(requested_quantity)
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError("requested_quantity must be finite and positive")
        price = (
            reference_price if reference_price is not None else getattr(bar, "o", None)
        )
        if price is None:
            return FillDecision(
                0,
                None,
                "UNFILLED",
                "VOLUME_UNAVAILABLE",
                self.model_ref,
                self.model_version,
                (),
                {},
                requested,
                0,
                requested,
                reason_code="NO_LIQUIDITY",
            )
        return FillDecision(
            requested,
            price,
            "FILLED",
            "FILLED",
            self.model_ref,
            self.model_version,
            (),
            {},
            requested,
            requested,
            0,
            reason_code="FILLED",
        )


@dataclass(frozen=True)
class VolumeParticipationFillModel(FillModel):
    """Opt-in model accepting a fraction of one bar's displayed volume."""

    participation_rate: float = 1.0
    lot_size: int = 1
    min_quantity: float = 0.0
    model_ref: str = "volume_participation_v1"
    model_version: str = "1"

    def __post_init__(self) -> None:
        rate = float(self.participation_rate)
        if not math.isfinite(rate) or rate < 0 or rate > 1:
            raise ValueError("participation_rate must be finite and in [0, 1]")
        if int(self.lot_size) <= 0:
            raise ValueError("lot_size must be positive")
        minimum = float(self.min_quantity)
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError("min_quantity must be finite and non-negative")
        object.__setattr__(self, "participation_rate", rate)
        object.__setattr__(self, "lot_size", int(self.lot_size))
        object.__setattr__(self, "min_quantity", minimum)

    def decide(
        self,
        intent: Any,
        bar: Any,
        requested_quantity: float,
        reference_price: Optional[float] = None,
    ) -> FillDecision:
        """Evaluate an order with the original stateless public signature."""
        return self.decide_with_budget(intent, bar, requested_quantity, reference_price)

    def decide_with_budget(
        self,
        intent: Any,
        bar: Any,
        requested_quantity: float,
        reference_price: Optional[float] = None,
        available_quantity: Optional[float] = None,
    ) -> FillDecision:
        """Decide one fill against an optional engine-owned remaining bar budget."""
        requested = float(requested_quantity)
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError("requested_quantity must be finite and positive")
        volume = getattr(bar, "v", None)
        if volume is None or not math.isfinite(float(volume)) or float(volume) <= 0:
            return FillDecision(
                0,
                None,
                "UNFILLED",
                "VOLUME_UNAVAILABLE",
                self.model_ref,
                self.model_version,
                ("volume",),
                {"bar_volume": volume},
                requested,
                0,
                requested,
                reason_code="NO_LIQUIDITY",
            )
        capacity = (
            math.floor(float(volume) * self.participation_rate / self.lot_size)
            * self.lot_size
        )
        if available_quantity is not None:
            remaining = float(available_quantity)
            if not math.isfinite(remaining) or remaining < 0:
                raise ValueError("available_quantity must be finite and non-negative")
            capacity = min(capacity, remaining)
        available = math.floor(capacity / self.lot_size) * self.lot_size
        quantity = (
            math.floor(min(requested, float(available)) / self.lot_size) * self.lot_size
        )
        if quantity < self.min_quantity or quantity <= 0:
            return FillDecision(
                0,
                None,
                "UNFILLED",
                "BELOW_MINIMUM",
                self.model_ref,
                self.model_version,
                ("volume",),
                {"bar_volume": float(volume), "available_quantity": float(available)},
                requested,
                float(available),
                requested,
                reason_code="NO_LIQUIDITY",
            )
        price = (
            reference_price if reference_price is not None else getattr(bar, "o", None)
        )
        status = "FILLED" if quantity >= requested else "PARTIAL"
        legacy_reason = "FILLED" if status == "FILLED" else "PARTIAL_VOLUME"
        canonical = "FILLED" if status == "FILLED" else "PARTIAL_FILL"
        return FillDecision(
            quantity,
            price,
            status,
            legacy_reason,
            self.model_ref,
            self.model_version,
            ("volume",),
            {
                "bar_volume": float(volume),
                "available_quantity": float(available),
                "participation_rate": self.participation_rate,
            },
            requested,
            float(available),
            max(0.0, requested - quantity),
            reason_code=canonical,
        )


__all__ = [
    "AllOrNoneFillModel",
    "FillDecision",
    "FillModel",
    "VolumeParticipationFillModel",
]
