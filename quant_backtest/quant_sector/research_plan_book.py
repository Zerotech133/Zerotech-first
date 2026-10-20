"""Append-only, in-memory research plans and meeting facts for offline replay.

No object in this module is an authorization, notification, order or trade. In
particular, a research conclusion does not replace dual_gate.ResearchReview or
its separately required human authorization and RiskGuard verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Tuple


_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SIDES = frozenset(("BUY", "SELL"))
_MEETING_STATES = frozenset(("HELD", "NOT_HELD", "TIMED_OUT"))
_REVIEW_STATES = frozenset(("CONFIRMED", "REJECTED", "NEEDS_EVIDENCE"))
_OPINIONS = frozenset(("SUPPORT", "OPPOSE", "ABSTAIN"))
_ZERO = timedelta(0)


class ResearchPlanError(ValueError):
    """Invalid offline research event or conflicting append-only identity."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise ResearchPlanError(field + "_INVALID")


def _text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 2048:
        raise ResearchPlanError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != _ZERO:
        raise ResearchPlanError(field + "_UTC_REQUIRED")


@dataclass(frozen=True)
class PlanRef:
    """Exact immutable identity for a research plan version."""

    plan_id: str
    version: int

    def __post_init__(self) -> None:
        _label(self.plan_id, "PLAN_ID")
        if type(self.version) is not int or self.version < 1:
            raise ResearchPlanError("PLAN_VERSION_INVALID")


@dataclass(frozen=True)
class ResearchPlan:
    """Abstract profile template, not a real customer's position instruction."""

    ref: PlanRef
    market: str
    symbol: str
    side: str
    thesis: str
    entry_condition: str
    exit_condition: str
    profile_id: str
    profile_version: str
    risk_budget_description: str
    published_at: datetime
    effective_from: datetime
    effective_until: datetime
    approver_id: str

    def __post_init__(self) -> None:
        if type(self.ref) is not PlanRef:
            raise ResearchPlanError("PLAN_REF_INVALID")
        for name in ("market", "symbol", "profile_id", "profile_version", "approver_id"):
            _label(getattr(self, name), name.upper())
        if type(self.side) is not str or self.side not in _SIDES:
            raise ResearchPlanError("SIDE_INVALID")
        for name in ("thesis", "entry_condition", "exit_condition", "risk_budget_description"):
            _text(getattr(self, name), name.upper())
        for name in ("published_at", "effective_from", "effective_until"):
            _utc(getattr(self, name), name.upper())
        if not self.published_at <= self.effective_from < self.effective_until:
            raise ResearchPlanError("PLAN_WINDOW_INVALID")


@dataclass(frozen=True)
class PlanRevocation:
    """Append-only tombstone; the original plan is never edited."""

    revocation_id: str
    ref: PlanRef
    recorded_at: datetime
    reason: str

    def __post_init__(self) -> None:
        _label(self.revocation_id, "REVOCATION_ID")
        if type(self.ref) is not PlanRef:
            raise ResearchPlanError("PLAN_REF_INVALID")
        _utc(self.recorded_at, "RECORDED_AT")
        _text(self.reason, "REASON")


@dataclass(frozen=True)
class ResearchSignal:
    """Frozen signal identity and both market and researcher-visible times."""

    signal_id: str
    market: str
    symbol: str
    side: str
    profile_id: str
    profile_version: str
    triggered_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("signal_id", "market", "symbol", "profile_id", "profile_version"):
            _label(getattr(self, name), name.upper())
        if type(self.side) is not str or self.side not in _SIDES:
            raise ResearchPlanError("SIDE_INVALID")
        _utc(self.triggered_at, "TRIGGERED_AT")
        _utc(self.observed_at, "OBSERVED_AT")
        if self.triggered_at > self.observed_at:
            raise ResearchPlanError("SIGNAL_TIME_ORDER_INVALID")


@dataclass(frozen=True)
class PlanMatch:
    """Signal-time research snapshot, never an execution permission."""

    signal: ResearchSignal
    status: str
    plan_ref: PlanRef | None = None
    conflicting_refs: Tuple[PlanRef, ...] = ()

    @property
    def trading_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False


@dataclass(frozen=True)
class ResearchOpinion:
    """An individual pre-meeting position retained despite later consensus."""

    participant_id: str
    position: str
    reason: str

    def __post_init__(self) -> None:
        _label(self.participant_id, "PARTICIPANT_ID")
        if type(self.position) is not str or self.position not in _OPINIONS:
            raise ResearchPlanError("POSITION_INVALID")
        _text(self.reason, "OPINION_REASON")


@dataclass(frozen=True)
class ResearchReviewFact:
    """Research-only quick review; NOT dual_gate.ResearchReview or authorization."""

    review_id: str
    signal_id: str
    decision: str
    reason: str
    recorded_at: datetime
    plan_ref: PlanRef | None = None

    def __post_init__(self) -> None:
        _label(self.review_id, "REVIEW_ID")
        _label(self.signal_id, "SIGNAL_ID")
        if type(self.decision) is not str or self.decision not in _REVIEW_STATES:
            raise ResearchPlanError("REVIEW_DECISION_INVALID")
        _text(self.reason, "REVIEW_REASON")
        _utc(self.recorded_at, "RECORDED_AT")
        if self.plan_ref is not None and type(self.plan_ref) is not PlanRef:
            raise ResearchPlanError("PLAN_REF_INVALID")


@dataclass(frozen=True)
class MeetingFact:
    """A held meeting or an explicit not-held/timeout fact, never backdated."""

    meeting_id: str
    signal_id: str
    status: str
    agenda: str
    decision: str
    before_plan_refs: Tuple[PlanRef, ...]
    opinions: Tuple[ResearchOpinion, ...]
    decided_at: datetime
    effective_at: datetime
    after_plan_ref: PlanRef | None = None
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        _label(self.meeting_id, "MEETING_ID")
        _label(self.signal_id, "SIGNAL_ID")
        if type(self.status) is not str or self.status not in _MEETING_STATES:
            raise ResearchPlanError("MEETING_STATUS_INVALID")
        _text(self.agenda, "AGENDA")
        _text(self.decision, "DECISION")
        if type(self.before_plan_refs) is not tuple or any(
            type(ref) is not PlanRef for ref in self.before_plan_refs
        ) or len(set(self.before_plan_refs)) != len(self.before_plan_refs):
            raise ResearchPlanError("BEFORE_PLAN_REFS_INVALID")
        if type(self.opinions) is not tuple or any(
            type(opinion) is not ResearchOpinion for opinion in self.opinions
        ) or len({opinion.participant_id for opinion in self.opinions}) != len(self.opinions):
            raise ResearchPlanError("OPINIONS_INVALID")
        _utc(self.decided_at, "DECIDED_AT")
        _utc(self.effective_at, "EFFECTIVE_AT")
        if self.effective_at < self.decided_at:
            raise ResearchPlanError("MEETING_BACKDATED")
        if self.deadline_at is not None:
            _utc(self.deadline_at, "DEADLINE_AT")
        if self.after_plan_ref is not None and type(self.after_plan_ref) is not PlanRef:
            raise ResearchPlanError("AFTER_PLAN_REF_INVALID")
        if self.status == "HELD":
            if not self.opinions or self.deadline_at is not None:
                raise ResearchPlanError("HELD_MEETING_FACT_INVALID")
        elif self.opinions or self.after_plan_ref is not None or self.effective_at != self.decided_at:
            raise ResearchPlanError("ABSENT_MEETING_CANNOT_REVISE")
        if self.status == "TIMED_OUT" and (
            self.deadline_at is None or self.deadline_at > self.decided_at
        ):
            raise ResearchPlanError("TIMEOUT_DEADLINE_INVALID")
        if self.status == "NOT_HELD" and self.deadline_at is not None:
            raise ResearchPlanError("NOT_HELD_DEADLINE_INVALID")


class ResearchPlanBook:
    """Single-process offline event book; no durable or authority semantics."""

    def __init__(self) -> None:
        self._plans: Dict[PlanRef, ResearchPlan] = {}
        self._revocations: Dict[str, PlanRevocation] = {}
        self._revoked_refs: Dict[PlanRef, PlanRevocation] = {}
        self._signals: Dict[str, PlanMatch] = {}
        self._meetings: Dict[str, MeetingFact] = {}
        self._reviews: Dict[str, ResearchReviewFact] = {}
        self._last_recorded_at: datetime | None = None

    def _check_clock(self, at: datetime) -> None:
        _utc(at, "RECORDED_AT")
        if self._last_recorded_at is not None and at < self._last_recorded_at:
            raise ResearchPlanError("HISTORY_BACKFILL_FORBIDDEN")

    def _advance_clock(self, at: datetime) -> None:
        self._last_recorded_at = at

    def publish(self, plan: ResearchPlan) -> ResearchPlan:
        """Append a published version; never edit or overwrite another version."""
        if type(plan) is not ResearchPlan:
            raise ResearchPlanError("PLAN_INVALID")
        existing = self._plans.get(plan.ref)
        if existing is not None:
            if existing == plan:
                return existing
            raise ResearchPlanError("PLAN_IDENTITY_CONFLICT")
        self._check_clock(plan.published_at)
        ancestors = [candidate for candidate in self._plans.values()
                     if candidate.ref.plan_id == plan.ref.plan_id]
        if ancestors:
            latest = max(ancestors, key=lambda candidate: candidate.ref.version)
            if plan.ref.version != latest.ref.version + 1:
                raise ResearchPlanError("PLAN_VERSION_NOT_NEXT")
            if (plan.market, plan.symbol, plan.side, plan.profile_id, plan.profile_version) != (
                latest.market, latest.symbol, latest.side, latest.profile_id, latest.profile_version
            ):
                raise ResearchPlanError("PLAN_SCOPE_CHANGED")
            if plan.effective_from < latest.effective_from:
                raise ResearchPlanError("PLAN_VERSION_BACKDATED")
        elif plan.ref.version != 1:
            raise ResearchPlanError("PLAN_FIRST_VERSION_INVALID")
        self._plans[plan.ref] = plan
        self._advance_clock(plan.published_at)
        return plan

    def revoke(self, event: PlanRevocation) -> PlanRevocation:
        """Append an effective-at-recording-time tombstone for one version."""
        if type(event) is not PlanRevocation:
            raise ResearchPlanError("REVOCATION_INVALID")
        existing = self._revocations.get(event.revocation_id)
        if existing is not None:
            if existing == event:
                return existing
            raise ResearchPlanError("REVOCATION_IDENTITY_CONFLICT")
        self._check_clock(event.recorded_at)
        plan = self._plans.get(event.ref)
        if plan is None or event.recorded_at < plan.published_at:
            raise ResearchPlanError("PLAN_NOT_PUBLISHED")
        if event.ref in self._revoked_refs:
            raise ResearchPlanError("PLAN_ALREADY_REVOKED")
        self._revocations[event.revocation_id] = event
        self._revoked_refs[event.ref] = event
        self._advance_clock(event.recorded_at)
        return event

    def _resolve(self, signal: ResearchSignal) -> PlanMatch:
        # Pick the latest *visible* version within each lineage first. A
        # revoked/latest version cannot fall back silently to its old version.
        lineages: Dict[str, ResearchPlan] = {}
        for plan in self._plans.values():
            if (plan.market, plan.symbol, plan.side, plan.profile_id, plan.profile_version) != (
                signal.market, signal.symbol, signal.side, signal.profile_id, signal.profile_version
            ) or plan.published_at > signal.triggered_at or plan.effective_from > signal.triggered_at:
                continue
            latest = lineages.get(plan.ref.plan_id)
            if latest is None or plan.ref.version > latest.ref.version:
                lineages[plan.ref.plan_id] = plan
        # The plan must have existed at trigger AND remain current when the
        # delayed signal reaches research; a tombstone is never ignored merely
        # because its time is later than the market event.
        active = tuple(sorted((plan.ref for plan in lineages.values()
                               if signal.observed_at < plan.effective_until
                               and (plan.ref not in self._revoked_refs or
                                    self._revoked_refs[plan.ref].recorded_at > signal.observed_at)),
                              key=lambda ref: (ref.plan_id, ref.version)))
        if len(active) > 1:
            return PlanMatch(signal, "CONFLICT", conflicting_refs=active)
        if active:
            return PlanMatch(signal, "MATCHED", plan_ref=active[0])
        return PlanMatch(signal, "NO_VALID_PLAN")

    def match_signal(self, signal: ResearchSignal) -> PlanMatch:
        """Freeze a research-only match against a caller-supplied offline signal.

        Market and symbol must both match. This does not verify G1 evidence or
        G2 acceptance; callers must not treat the match as authorization.
        """
        if type(signal) is not ResearchSignal:
            raise ResearchPlanError("SIGNAL_INVALID")
        existing = self._signals.get(signal.signal_id)
        if existing is not None:
            if existing.signal == signal:
                return existing
            raise ResearchPlanError("SIGNAL_IDENTITY_CONFLICT")
        self._check_clock(signal.observed_at)
        match = self._resolve(signal)
        self._signals[signal.signal_id] = match
        self._advance_clock(signal.observed_at)
        return match

    def review(self, fact: ResearchReviewFact) -> ResearchReviewFact:
        """Record an independent research opinion, without granting authority."""
        if type(fact) is not ResearchReviewFact:
            raise ResearchPlanError("REVIEW_INVALID")
        existing = self._reviews.get(fact.review_id)
        if existing is not None:
            if existing == fact:
                return existing
            raise ResearchPlanError("REVIEW_IDENTITY_CONFLICT")
        self._check_clock(fact.recorded_at)
        match = self._signals.get(fact.signal_id)
        if match is None or fact.recorded_at < match.signal.observed_at:
            raise ResearchPlanError("SIGNAL_NOT_OBSERVED")
        if fact.plan_ref is not None and fact.plan_ref not in (
            (match.plan_ref,) if match.plan_ref is not None else match.conflicting_refs
        ):
            raise ResearchPlanError("REVIEW_PLAN_NOT_VISIBLE_AT_SIGNAL")
        self._reviews[fact.review_id] = fact
        self._advance_clock(fact.recorded_at)
        return fact

    def record_meeting(self, fact: MeetingFact, new_plan: ResearchPlan | None = None) -> MeetingFact:
        """Atomically append a meeting and optional forward-effective plan version."""
        if type(fact) is not MeetingFact:
            raise ResearchPlanError("MEETING_INVALID")
        existing = self._meetings.get(fact.meeting_id)
        if existing is not None:
            if existing == fact and (new_plan is None or self._plans.get(new_plan.ref) == new_plan):
                return existing
            raise ResearchPlanError("MEETING_IDENTITY_CONFLICT")
        self._check_clock(fact.decided_at)
        match = self._signals.get(fact.signal_id)
        if match is None or fact.decided_at < match.signal.observed_at:
            raise ResearchPlanError("SIGNAL_NOT_OBSERVED")
        available = ((match.plan_ref,) if match.plan_ref is not None else match.conflicting_refs)
        if fact.before_plan_refs != available:
            raise ResearchPlanError("BEFORE_PLAN_REFS_MISMATCH")
        if fact.deadline_at is not None and fact.deadline_at < match.signal.observed_at:
            raise ResearchPlanError("DEADLINE_BEFORE_SIGNAL")
        if (new_plan is None) != (fact.after_plan_ref is None):
            raise ResearchPlanError("MEETING_PLAN_BINDING_INVALID")
        if new_plan is not None:
            if type(new_plan) is not ResearchPlan or new_plan.ref != fact.after_plan_ref:
                raise ResearchPlanError("MEETING_PLAN_BINDING_INVALID")
            if (new_plan.market, new_plan.symbol, new_plan.side,
                    new_plan.profile_id, new_plan.profile_version) != (
                match.signal.market, match.signal.symbol, match.signal.side,
                match.signal.profile_id, match.signal.profile_version
            ):
                raise ResearchPlanError("MEETING_PLAN_SCOPE_MISMATCH")
            if new_plan.published_at != fact.decided_at or new_plan.effective_from != fact.effective_at:
                raise ResearchPlanError("MEETING_PLAN_BACKDATED")
            if new_plan.ref in self._plans:
                raise ResearchPlanError("MEETING_PLAN_ALREADY_PUBLISHED")
            # publish performs all version checks before mutating its sole map;
            # record the meeting only after the new version is accepted.
            self.publish(new_plan)
        self._meetings[fact.meeting_id] = fact
        self._advance_clock(fact.decided_at)
        return fact

    def plan(self, ref: PlanRef) -> ResearchPlan | None:
        """Return an immutable version without implying current validity."""
        return self._plans.get(ref)

    def signal_match(self, signal_id: str) -> PlanMatch | None:
        """Return the original frozen signal-time match, not today's match."""
        _label(signal_id, "SIGNAL_ID")
        return self._signals.get(signal_id)

    def meetings_for(self, signal_id: str) -> Tuple[MeetingFact, ...]:
        """Return recorded meeting and non-meeting facts in append order."""
        _label(signal_id, "SIGNAL_ID")
        return tuple(fact for fact in self._meetings.values() if fact.signal_id == signal_id)

    def reviews_for(self, signal_id: str) -> Tuple[ResearchReviewFact, ...]:
        """Return quick reviews in append order, including dissent/rejection."""
        _label(signal_id, "SIGNAL_ID")
        return tuple(fact for fact in self._reviews.values() if fact.signal_id == signal_id)
