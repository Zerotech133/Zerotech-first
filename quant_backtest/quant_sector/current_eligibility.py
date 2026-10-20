"""As-of, read-only current-eligibility view over the research plan book.

This module is a pure projection (G3 分视图). It never writes to the plan
book, never manufactures authority and never grants execution, notification
or any other real-world permission. ``CURRENT_ELIGIBLE`` means only that, at
the explicitly requested ``as_of`` instant, a caller-supplied authority root
is active and the plan book holds exactly one unambiguous currently-effective
research plan version for the explicitly named market and symbol.

Market and symbol identity is always passed in explicitly by the caller; the
view never guesses a market from a symbol code suffix. A missing or malformed
authority root fails closed (``FAIL_CLOSED``); a root that is present but
revoked or otherwise inactive is ``NOT_ELIGIBLE``. A historical ``PlanMatch``
(such as a signal-time ``MATCHED`` attribution) is never consulted for the
verdict: current eligibility is always re-derived from the as-of state.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Tuple

from quant_backtest.quant_sector.research_plan_book import ResearchPlanBook

CURRENT_ELIGIBLE = "CURRENT_ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
FAIL_CLOSED = "FAIL_CLOSED"
_VERDICT_STATUSES = frozenset((CURRENT_ELIGIBLE, NOT_ELIGIBLE, FAIL_CLOSED))

_ACTIVE_AUTHORITY_STATUSES = frozenset(("ACTIVE",))
_INACTIVE_AUTHORITY_STATUSES = frozenset(("REVOKED", "INACTIVE", "EXPIRED"))

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ZERO = timedelta(0)


def _is_utc(value: object) -> bool:
    """Return True only for timezone-aware UTC datetimes."""
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() == _ZERO


def _is_label(value: object) -> bool:
    return type(value) is str and _LABEL.fullmatch(value) is not None


@dataclass(frozen=True)
class AuthorityRoot:
    """Caller-supplied authority reference; the view never creates one.

    Core fields (``signal_id``, ``event_id``, ``status``) must be supplied by
    the caller; this dataclass deliberately defaults every field to ``None``
    so that a partially-filled root can be constructed and then rejected by
    :func:`evaluate` with a ``FAIL_CLOSED`` verdict instead of raising.
    ``effective_from`` / ``effective_until`` optionally bound the root's own
    validity window; ``market`` / ``symbol`` optionally bind the root to one
    explicit market and symbol identity.
    """

    signal_id: Any = None
    event_id: Any = None
    status: Any = None
    effective_from: Any = None
    effective_until: Any = None
    market: Any = None
    symbol: Any = None


@dataclass(frozen=True)
class EligibilityVerdict:
    """Read-only as-of outcome; never an execution or notification permission."""

    status: str
    reason: str
    evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _VERDICT_STATUSES:
            raise ValueError("VERDICT_STATUS_INVALID")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("VERDICT_REASON_INVALID")
        if type(self.evidence_refs) is not tuple or any(
            type(ref) is not str for ref in self.evidence_refs
        ):
            raise ValueError("VERDICT_EVIDENCE_INVALID")

    @property
    def is_current_eligible(self) -> bool:
        return self.status == CURRENT_ELIGIBLE

    @property
    def is_fail_closed(self) -> bool:
        return self.status == FAIL_CLOSED

    @property
    def trading_permitted(self) -> bool:
        """Hard False: an eligibility verdict is never a trading permission."""
        return False

    @property
    def notification_permitted(self) -> bool:
        """Hard False: an eligibility verdict is never a notification permission."""
        return False


def _root_field(root: object, name: str) -> Any:
    """Read one root field from a mapping or attribute object, defaulting None."""
    if isinstance(root, Mapping):
        return root.get(name)
    return getattr(root, name, None)


def _fail(reason: str, evidence: Tuple[str, ...] = ()) -> EligibilityVerdict:
    return EligibilityVerdict(FAIL_CLOSED, reason, evidence)


def _not_eligible(reason: str, evidence: Tuple[str, ...]) -> EligibilityVerdict:
    return EligibilityVerdict(NOT_ELIGIBLE, reason, evidence)


def _authority_evidence(signal_id: str, event_id: str) -> list[str]:
    return ["authority:" + event_id, "signal:" + signal_id]


def _plan_ref(plan: Any) -> str:
    return "plan:%s:%d" % (plan.ref.plan_id, plan.ref.version)


def _current_plan(plans: dict, revoked: dict, market: str, symbol: str,
                  as_of: datetime) -> tuple[Any, ...]:
    """Resolve the active plan versions visible at ``as_of`` (read-only).

    Mirrors the plan book's own semantics: within each lineage only the
    highest visible version is considered (a revoked or expired latest
    version never falls back to an older one), and versions published or
    made effective after ``as_of`` are invisible (no lookahead).
    """
    lineages: dict[str, Any] = {}
    for plan in plans.values():
        if plan.market != market or plan.symbol != symbol:
            continue
        if plan.published_at > as_of or plan.effective_from > as_of:
            continue
        latest = lineages.get(plan.ref.plan_id)
        if latest is None or plan.ref.version > latest.ref.version:
            lineages[plan.ref.plan_id] = plan
    active = []
    for plan_id in sorted(lineages):
        plan = lineages[plan_id]
        tombstone = revoked.get(plan.ref)
        if tombstone is not None and tombstone.recorded_at <= as_of:
            continue
        if as_of >= plan.effective_until:
            continue
        active.append(plan)
    return tuple(active)


def evaluate(plan_book: object, authority_root: object, market: str,
             symbol: str, as_of: datetime) -> EligibilityVerdict:
    """Re-derive current follow eligibility at ``as_of``; pure read, no writes.

    The verdict is a deterministic function of the arguments. Historical
    ``PlanMatch`` records are ignored: only the as-of highest effective plan
    version plus the caller-supplied authority root's current status decide
    the outcome.
    """
    # -- 1. explicit as-of instant and identity (never inferred) ------------
    if not _is_utc(as_of):
        return _fail("AS_OF_UTC_REQUIRED")
    if not _is_label(market):
        return _fail("MARKET_INVALID")
    if not _is_label(symbol):
        return _fail("SYMBOL_INVALID")

    # -- 2. authority root must exist and carry core fields -----------------
    if authority_root is None:
        return _fail("AUTHORITY_ROOT_MISSING")
    signal_id = _root_field(authority_root, "signal_id")
    event_id = _root_field(authority_root, "event_id")
    status = _root_field(authority_root, "status")
    if not _is_label(signal_id) or not _is_label(event_id) \
            or type(status) is not str or not status:
        return _fail("AUTHORITY_ROOT_CORE_INVALID")
    evidence = _authority_evidence(signal_id, event_id)

    effective_from = _root_field(authority_root, "effective_from")
    effective_until = _root_field(authority_root, "effective_until")
    if (effective_from is not None and not _is_utc(effective_from)) or \
            (effective_until is not None and not _is_utc(effective_until)):
        return _fail("AUTHORITY_WINDOW_INVALID", tuple(evidence))
    root_market = _root_field(authority_root, "market")
    root_symbol = _root_field(authority_root, "symbol")
    if root_market is not None and not _is_label(root_market):
        return _fail("AUTHORITY_ROOT_MARKET_INVALID", tuple(evidence))
    if root_symbol is not None and not _is_label(root_symbol):
        return _fail("AUTHORITY_ROOT_SYMBOL_INVALID", tuple(evidence))

    # -- 3. the plan book must be a readable ResearchPlanBook ---------------
    if not isinstance(plan_book, ResearchPlanBook):
        return _fail("PLAN_BOOK_INVALID")
    plans = getattr(plan_book, "_plans", None)
    revoked = getattr(plan_book, "_revoked_refs", None)
    if type(plans) is not dict or type(revoked) is not dict:
        return _fail("PLAN_BOOK_UNREADABLE")

    # -- 4. authority root current status -----------------------------------
    if status in _INACTIVE_AUTHORITY_STATUSES:
        return _not_eligible("AUTHORITY_" + status, tuple(evidence))
    if status not in _ACTIVE_AUTHORITY_STATUSES:
        return _fail("AUTHORITY_STATUS_UNRECOGNIZED", tuple(evidence))

    # -- 5. authority window and explicit binding isolation -----------------
    if effective_from is not None and as_of < effective_from:
        return _not_eligible("AUTHORITY_WINDOW_NOT_STARTED", tuple(evidence))
    if effective_until is not None and as_of >= effective_until:
        return _not_eligible("AUTHORITY_WINDOW_ENDED", tuple(evidence))
    if root_market is not None and root_market != market:
        return _not_eligible("AUTHORITY_MARKET_MISMATCH", tuple(evidence))
    if root_symbol is not None and root_symbol != symbol:
        return _not_eligible("AUTHORITY_SYMBOL_MISMATCH", tuple(evidence))

    # -- 6. as-of current plan resolution ------------------------------------
    active = _current_plan(plans, revoked, market, symbol, as_of)
    if len(active) > 1:
        conflicted = tuple(sorted(_plan_ref(plan) for plan in active))
        return _fail("CURRENT_PLAN_CONFLICT", tuple(evidence) + conflicted)
    if not active:
        return _not_eligible("NO_CURRENT_PLAN", tuple(evidence))

    evidence.append(_plan_ref(active[0]))
    return EligibilityVerdict(CURRENT_ELIGIBLE, "CURRENT_PLAN_ACTIVE",
                              tuple(evidence))
