"""Offline append-only evidence store. Memory projection, not a persistence root."""
from __future__ import annotations

from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope


class EvidenceConflict(ValueError):
    """Raised when frozen identity is reused with different content."""


class EvidenceStore:
    """Freeze-once identity store: same identity+bytes is idempotent, divergence fails."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, ...], dict[int, EvidenceEnvelope]] = {}

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @staticmethod
    def identity(envelope: EvidenceEnvelope) -> tuple[str, ...]:
        return (envelope.pool, envelope.tenant_or_owner_id, envelope.market,
                envelope.code, envelope.event_id)

    def freeze(self, envelope: EvidenceEnvelope, *, now=None) -> EvidenceEnvelope:
        """Validate then store; version>1 must chain onto the stored predecessor."""
        if envelope.evidence_version > 1:
            stored = self._versions.get(self.identity(envelope), {}).get(
                envelope.evidence_version - 1)
            if stored is None:
                raise EvidenceConflict("PREVIOUS_NOT_FROZEN")
            envelope.validate(now=now, previous=stored)
        else:
            envelope.validate(now=now)
        identity = self.identity(envelope)
        versions = self._versions.setdefault(identity, {})
        existing = versions.get(envelope.evidence_version)
        if existing is not None:
            if existing == envelope:
                return existing
            raise EvidenceConflict("EVIDENCE_CONFLICT")
        versions[envelope.evidence_version] = envelope
        return envelope

    def get_exact(self, envelope: EvidenceEnvelope) -> EvidenceEnvelope | None:
        return self._versions.get(self.identity(envelope), {}).get(envelope.evidence_version)

    def latest(self, envelope: EvidenceEnvelope) -> EvidenceEnvelope | None:
        versions = self._versions.get(self.identity(envelope), {})
        return versions[max(versions)] if versions else None
