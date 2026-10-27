"""Non-actionable offline consumer of independently supplied WATCH source evidence.

This module does not establish the provenance or independence of an authority.
It has no publishing, notification, simulation handoff, or trading capability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from quant_backtest.watch_source_authority import (
    SourceQualification,
    WatchSourceAuthority,
    verify_watch_sources,
)


class OfflineConsumptionDiagnosis:
    """An offline-only diagnosis; action gates cannot be constructor arguments."""

    __slots__ = ("_status", "_code")

    def __init__(self, status: str, code: str) -> None:
        if type(status) is not str or type(code) is not str:
            raise TypeError("Diagnosis status and code must be strings")
        object.__setattr__(self, "_status", status)
        object.__setattr__(self, "_code", code)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Offline diagnoses are immutable")

    @property
    def status(self) -> str:
        """Return the offline-only qualification status."""
        return self._status

    @property
    def code(self) -> str:
        """Return the verifier's validated diagnostic code."""
        return self._code

    @property
    def production_permitted(self) -> bool:
        """Never grant production use."""
        return False

    @property
    def notify_allowed(self) -> bool:
        """Never grant notification delivery."""
        return False

    @property
    def sim_handoff_allowed(self) -> bool:
        """Never grant simulation handoff."""
        return False


def consume_watch_offline_sources(
    events: Sequence[Mapping[str, Any]],
    *,
    evaluation_as_of: datetime,
    authority: WatchSourceAuthority | None,
) -> OfflineConsumptionDiagnosis:
    """Evaluate externally injected evidence without turning it into an action.

    The authority must be passed separately; alert/event proof fields are never
    used to construct or replace it. The underlying verifier owns all source,
    membership, time, calendar, and provider checks. Unexpected failures fail
    closed rather than leaking an exception into a downstream decision path.
    """
    unavailable = OfflineConsumptionDiagnosis(
        "SOURCE_UNVERIFIED", "AUTHORITY_UNAVAILABLE"
    )
    if authority is None:
        return unavailable
    if type(evaluation_as_of) is not datetime or evaluation_as_of.tzinfo is None:
        return OfflineConsumptionDiagnosis("SOURCE_UNVERIFIED", "EVALUATION_TIME")
    try:
        offset = evaluation_as_of.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            return OfflineConsumptionDiagnosis("SOURCE_UNVERIFIED", "EVALUATION_TIME")
        result = verify_watch_sources(
            events, evaluation_as_of=evaluation_as_of, authority=authority
        )
        if (
            type(result) is not SourceQualification
            or type(result.status) is not str
            or type(result.code) is not str
        ):
            return unavailable
        if result.status == "QUALIFIED_OFFLINE" and result.code == "OK":
            return OfflineConsumptionDiagnosis(result.status, result.code)
        if result.status == "SOURCE_UNVERIFIED" and result.code and result.code != "OK":
            return OfflineConsumptionDiagnosis(result.status, result.code)
    except Exception:
        return unavailable
    return unavailable
