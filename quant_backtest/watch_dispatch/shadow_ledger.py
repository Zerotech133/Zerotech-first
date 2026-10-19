"""Append-only shadow decision ledger for trader training. Never an order book.

Decisions record what was visible and assumed at decision time; reviews are
appended afterwards with posterior information. No order, fill, PnL or
authorization identifier exists anywhere in this domain, and no entry is
executable. This ledger never doubles as the trading agent's books.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class ShadowLedgerError(ValueError):
    """Fail-closed shadow ledger violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise ShadowLedgerError(field + "_INVALID")


def _text(value: object, field: str, limit: int) -> None:
    if type(value) is not str or not value or len(value) > limit:
        raise ShadowLedgerError(field + "_INVALID")


def _refs(value: object, field: str) -> None:
    if type(value) is not tuple or not value:
        raise ShadowLedgerError(field + "_EMPTY")
    for item in value:
        _text(item, field, 512)


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowLedgerError(field + "_INVALID")
    utc_text(value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ShadowDecision:
    shadow_id: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    decision_at: datetime
    hypothesis: str
    planned_action: str
    invalidation_condition: str
    visible_evidence_refs: Tuple[str, ...]
    non_executable: bool = True

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @property
    def production_permitted(self) -> bool:
        return False

    def __post_init__(self) -> None:
        if self.non_executable is not True:
            raise ShadowLedgerError("EXECUTION_FORBIDDEN")
        _label(self.shadow_id, "SHADOW_ID")
        _label(self.event_id, "EVENT_ID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise ShadowLedgerError("VERSION_INVALID")
        if type(self.envelope_hash) is not str or len(self.envelope_hash) != 64:
            raise ShadowLedgerError("ENVELOPE_HASH_INVALID")
        _utc(self.decision_at, "DECISION_AT")
        _text(self.hypothesis, "HYPOTHESIS", 2048)
        _text(self.planned_action, "PLANNED_ACTION", 2048)
        _text(self.invalidation_condition, "INVALIDATION_CONDITION", 2048)
        _refs(self.visible_evidence_refs, "VISIBLE_EVIDENCE_REFS")

    def binds(self, envelope: EvidenceEnvelope) -> bool:
        return (self.event_id == envelope.event_id
                and self.evidence_version == envelope.evidence_version
                and self.envelope_hash == envelope.envelope_hash)


@dataclass(frozen=True)
class ShadowReview:
    review_id: str
    shadow_id: str
    reviewed_at: datetime
    posterior_refs: Tuple[str, ...]
    hindsight_note: str

    def __post_init__(self) -> None:
        _label(self.review_id, "REVIEW_ID")
        _label(self.shadow_id, "SHADOW_ID")
        _utc(self.reviewed_at, "REVIEWED_AT")
        _refs(self.posterior_refs, "POSTERIOR_REFS")
        _text(self.hindsight_note, "HINDSIGHT_NOTE", 2048)


@dataclass(frozen=True)
class ShadowView:
    decision: ShadowDecision
    reviews_up_to: Tuple[ShadowReview, ...]


class ShadowLedger:
    """In-memory append-only projection; production needs an immutable root."""

    def __init__(self) -> None:
        self._decisions: Dict[str, ShadowDecision] = {}
        self._reviews: Dict[str, List[ShadowReview]] = {}
        self._review_ids: set[str] = set()

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def append_decision(self, envelope: EvidenceEnvelope,
                        decision: ShadowDecision) -> None:
        envelope.validate(now=decision.decision_at)
        if not decision.binds(envelope):
            raise ShadowLedgerError("DECISION_IDENTITY_MISMATCH")
        if decision.shadow_id in self._decisions:
            raise ShadowLedgerError("DECISION_EXISTS")
        self._decisions[decision.shadow_id] = decision
        self._reviews[decision.shadow_id] = []

    def append_review(self, envelope: EvidenceEnvelope,
                      review: ShadowReview) -> None:
        decision = self._decisions.get(review.shadow_id)
        if decision is None or not decision.binds(envelope):
            raise ShadowLedgerError("REVIEW_IDENTITY_MISMATCH")
        if review.review_id in self._review_ids:
            raise ShadowLedgerError("REVIEW_EXISTS")
        if review.reviewed_at < decision.decision_at:
            raise ShadowLedgerError("REVIEW_BEFORE_DECISION")
        self._reviews[review.shadow_id].append(review)
        self._review_ids.add(review.review_id)

    def read_asof(self, envelope: EvidenceEnvelope,
                  as_of: datetime) -> Tuple[ShadowView, ...]:
        """Time-split view: only decisions and reviews visible at `as_of`."""
        _utc(as_of, "AS_OF")
        views: List[ShadowView] = []
        for shadow_id, decision in self._decisions.items():
            if not decision.binds(envelope) or decision.decision_at > as_of:
                continue
            visible = tuple(r for r in self._reviews[shadow_id]
                            if r.reviewed_at <= as_of)
            views.append(ShadowView(decision=decision, reviews_up_to=visible))
        return tuple(views)
