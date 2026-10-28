"""Offline adversarial matrix for the G3 current-eligibility read-only view."""
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.current_eligibility import (
    CURRENT_ELIGIBLE,
    FAIL_CLOSED,
    NOT_ELIGIBLE,
    AuthorityRoot,
    EligibilityVerdict,
    evaluate,
)
from quant_backtest.quant_sector.research_plan_book import (
    PlanRef,
    PlanRevocation,
    ResearchPlanBook,
    ResearchSignal,
)

T0 = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def plan(plan_id: str = "p1", version: int = 1, published: int = 0,
         start: int = 0, end: int = 120, **changes: object):
    values: dict[str, object] = dict(
        ref=PlanRef(plan_id, version), market="CN_A", symbol="600000.SH", side="BUY",
        thesis="synthetic hypothesis", entry_condition="synthetic entry evidence",
        exit_condition="synthetic exit evidence", profile_id="anonymous-conservative",
        profile_version="v1", risk_budget_description="abstract risk template",
        published_at=at(published), effective_from=at(start), effective_until=at(end),
        approver_id="offline-researcher",
    )
    values.update(changes)
    from quant_backtest.quant_sector.research_plan_book import ResearchPlan
    return ResearchPlan(**values)  # type: ignore[arg-type]


def signal(signal_id: str = "s1", minute: int = 10, **changes: object) -> ResearchSignal:
    values: dict[str, object] = dict(
        signal_id=signal_id, market="CN_A", symbol="600000.SH", side="BUY",
        profile_id="anonymous-conservative", profile_version="v1",
        triggered_at=at(minute), observed_at=at(minute),
    )
    values.update(changes)
    return ResearchSignal(**values)  # type: ignore[arg-type]


def active_root(**changes: object) -> AuthorityRoot:
    values: dict[str, object] = dict(
        signal_id="s1", event_id="evt-1", status="ACTIVE",
        effective_from=at(0), effective_until=None,
        market=None, symbol=None,
    )
    values.update(changes)
    return AuthorityRoot(**values)  # type: ignore[arg-type]


def book_with_v1_and_v2(v1_end: int = 120) -> ResearchPlanBook:
    book = ResearchPlanBook()
    book.publish(plan(end=v1_end))
    book.publish(plan(version=2, published=20, start=25, end=140))
    return book


# ---------------------------------------------------------------------------
# 1. No lookahead: future versions are invisible at as_of
# ---------------------------------------------------------------------------

def test_future_version_invisible_at_as_of() -> None:
    book = book_with_v1_and_v2()
    verdict = evaluate(book, active_root(), "CN_A", "600000.SH", at(20))
    assert verdict.status == CURRENT_ELIGIBLE
    assert "plan:p1:1" in verdict.evidence_refs
    assert "plan:p1:2" not in verdict.evidence_refs
    # v2 exists in the book but is neither published nor effective at minute 12;
    # v1 already expired at minute 10, so nothing is eligible and v2 stays hidden.
    future_only = ResearchPlanBook()
    future_only.publish(plan(end=10))
    future_only.publish(plan(version=2, published=20, start=25, end=140))
    hidden = evaluate(future_only, active_root(), "CN_A", "600000.SH", at(12))
    assert hidden.status == NOT_ELIGIBLE
    assert hidden.reason == "NO_CURRENT_PLAN"
    assert all(not ref.startswith("plan:") for ref in hidden.evidence_refs)


# ---------------------------------------------------------------------------
# 2. New version replaces old attribution; historical PlanMatch is not authority
# ---------------------------------------------------------------------------

def test_new_version_effective_replaces_old_attribution() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    historical = book.match_signal(signal("s1"))
    assert historical.status == "MATCHED" and historical.plan_ref == PlanRef("p1", 1)
    book.publish(plan(version=2, published=20, start=25, end=140))
    verdict = evaluate(book, active_root(), "CN_A", "600000.SH", at(30))
    assert verdict.status == CURRENT_ELIGIBLE
    assert "plan:p1:2" in verdict.evidence_refs
    assert "plan:p1:1" not in verdict.evidence_refs
    assert book.signal_match("s1") is historical  # frozen attribution untouched


def test_historical_matched_does_not_grant_current_eligibility() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    book.match_signal(signal("s1"))
    book.revoke(PlanRevocation("rv1", PlanRef("p1", 1), at(15), "hypothesis withdrawn"))
    assert book.signal_match("s1").status == "MATCHED"  # type: ignore[union-attr]
    verdict = evaluate(book, active_root(), "CN_A", "600000.SH", at(20))
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == "NO_CURRENT_PLAN"


# ---------------------------------------------------------------------------
# 3. Authority root lifecycle: revoked is NOT_ELIGIBLE, missing is FAIL_CLOSED
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "reason"), [
    ("REVOKED", "AUTHORITY_REVOKED"),
    ("INACTIVE", "AUTHORITY_INACTIVE"),
    ("EXPIRED", "AUTHORITY_EXPIRED"),
])
def test_authority_root_revoked_or_inactive_is_not_eligible(
    status: str, reason: str,
) -> None:
    book = book_with_v1_and_v2()
    verdict = evaluate(book, active_root(status=status), "CN_A", "600000.SH", at(30))
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == reason
    missing = evaluate(book, None, "CN_A", "600000.SH", at(30))
    assert missing.status == FAIL_CLOSED  # distinct from revoked
    assert missing.reason == "AUTHORITY_ROOT_MISSING"


@pytest.mark.parametrize("bad_root", [
    None,
    object(),
    AuthorityRoot(signal_id="s1"),                      # missing event/status
    AuthorityRoot(signal_id="s1", event_id="evt-1"),    # missing status
    AuthorityRoot(signal_id="s1", event_id="evt-1", status=""),
    AuthorityRoot(signal_id=None, event_id="evt-1", status="ACTIVE"),
    AuthorityRoot(signal_id="s1", event_id="evt-1", status="PENDING"),
    AuthorityRoot(signal_id="s1", event_id="evt-1", status="ACTIVE",
                  effective_until="soon"),
])
def test_missing_or_malformed_root_fails_closed(bad_root: object) -> None:
    book = book_with_v1_and_v2()
    verdict = evaluate(book, bad_root, "CN_A", "600000.SH", at(30))
    assert verdict.status == FAIL_CLOSED
    assert verdict.reason in (
        "AUTHORITY_ROOT_MISSING", "AUTHORITY_ROOT_CORE_INVALID",
        "AUTHORITY_STATUS_UNRECOGNIZED", "AUTHORITY_WINDOW_INVALID",
    )


def test_unrecognized_root_status_fails_closed_not_not_eligible() -> None:
    book = book_with_v1_and_v2()
    verdict = evaluate(book, active_root(status="DEFERRED"),
                       "CN_A", "600000.SH", at(30))
    assert verdict.status == FAIL_CLOSED
    assert verdict.reason == "AUTHORITY_STATUS_UNRECOGNIZED"


# ---------------------------------------------------------------------------
# 4. Authority window and explicit binding isolation
# ---------------------------------------------------------------------------

def test_authority_window_inactive_is_not_eligible() -> None:
    book = book_with_v1_and_v2()
    ended = evaluate(book, active_root(effective_until=at(10)),
                     "CN_A", "600000.SH", at(15))
    assert ended.status == NOT_ELIGIBLE and ended.reason == "AUTHORITY_WINDOW_ENDED"
    not_started = evaluate(book, active_root(effective_from=at(50)),
                           "CN_A", "600000.SH", at(15))
    assert not_started.status == NOT_ELIGIBLE
    assert not_started.reason == "AUTHORITY_WINDOW_NOT_STARTED"


# ---------------------------------------------------------------------------
# 5. Cross-market and cross-symbol isolation; market never guessed from suffix
# ---------------------------------------------------------------------------

def test_cross_market_and_symbol_isolation() -> None:
    book = book_with_v1_and_v2()
    other_market = evaluate(book, active_root(), "US", "600000.SH", at(30))
    assert other_market.status == NOT_ELIGIBLE
    assert other_market.reason == "NO_CURRENT_PLAN"
    other_symbol = evaluate(book, active_root(), "CN_A", "000001.SZ", at(30))
    assert other_symbol.status == NOT_ELIGIBLE
    assert other_symbol.reason == "NO_CURRENT_PLAN"
    # an explicit root-to-market binding mismatch is surfaced, not absorbed
    bound = evaluate(book, active_root(market="HK_STOCK"),
                     "CN_A", "600000.SH", at(30))
    assert bound.status == NOT_ELIGIBLE
    assert bound.reason == "AUTHORITY_MARKET_MISMATCH"
    bound_symbol = evaluate(book, active_root(symbol="000001.SZ"),
                            "CN_A", "600000.SH", at(30))
    assert bound_symbol.status == NOT_ELIGIBLE
    assert bound_symbol.reason == "AUTHORITY_SYMBOL_MISMATCH"


# ---------------------------------------------------------------------------
# 6. Latest-version revocation/expiry never falls back to the old version
# ---------------------------------------------------------------------------

def test_latest_version_revoked_or_expired_never_falls_back() -> None:
    revoked = ResearchPlanBook()
    revoked.publish(plan(end=140))
    revoked.publish(plan(version=2, published=10, start=10, end=140))
    revoked.revoke(PlanRevocation("rv2", PlanRef("p1", 2), at(12), "invalid evidence"))
    verdict = evaluate(revoked, active_root(), "CN_A", "600000.SH", at(15))
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == "NO_CURRENT_PLAN"

    expired = ResearchPlanBook()
    expired.publish(plan(end=140))
    expired.publish(plan(version=2, published=10, start=10, end=15))
    verdict = evaluate(expired, active_root(), "CN_A", "600000.SH", at(16))
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == "NO_CURRENT_PLAN"


def test_conflicting_lineages_fail_closed() -> None:
    book = ResearchPlanBook()
    book.publish(plan("p1"))
    book.publish(plan("p2", published=1, start=1))
    verdict = evaluate(book, active_root(), "CN_A", "600000.SH", at(30))
    assert verdict.status == FAIL_CLOSED
    assert verdict.reason == "CURRENT_PLAN_CONFLICT"
    assert "plan:p1:1" in verdict.evidence_refs
    assert "plan:p2:1" in verdict.evidence_refs


# ---------------------------------------------------------------------------
# 7. Determinism of identical arguments
# ---------------------------------------------------------------------------

def test_same_arguments_deterministic() -> None:
    first_book = book_with_v1_and_v2()
    second_book = book_with_v1_and_v2()
    root = active_root()
    first = evaluate(first_book, root, "CN_A", "600000.SH", at(30))
    second = evaluate(first_book, root, "CN_A", "600000.SH", at(30))
    mirror = evaluate(second_book, root, "CN_A", "600000.SH", at(30))
    assert first == second == mirror
    assert first.evidence_refs == second.evidence_refs == mirror.evidence_refs
    not_first = evaluate(first_book, active_root(status="REVOKED"),
                         "CN_A", "600000.SH", at(30))
    assert not_first != first


# ---------------------------------------------------------------------------
# 8. Invalid inputs fail closed
# ---------------------------------------------------------------------------

def test_invalid_as_of_market_symbol_or_book_fail_closed() -> None:
    book = book_with_v1_and_v2()
    root = active_root()
    naive = evaluate(book, root, "CN_A", "600000.SH",
                     at(30).replace(tzinfo=None))
    assert naive.status == FAIL_CLOSED and naive.reason == "AS_OF_UTC_REQUIRED"
    assert evaluate(book, root, None, "600000.SH", at(30)).reason == "MARKET_INVALID"
    assert evaluate(book, root, "", "600000.SH", at(30)).reason == "MARKET_INVALID"
    assert evaluate(book, root, "CN_A", None, at(30)).reason == "SYMBOL_INVALID"
    assert evaluate(book, root, "CN_A", "", at(30)).reason == "SYMBOL_INVALID"
    assert evaluate(object(), root, "CN_A", "600000.SH",
                    at(30)).reason == "PLAN_BOOK_INVALID"
    assert all(v.status == FAIL_CLOSED for v in (naive,))


# ---------------------------------------------------------------------------
# 9. Zero write-back: evaluate never mutates the plan book
# ---------------------------------------------------------------------------

def test_evaluate_never_writes_plan_book() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    book.match_signal(signal("s1"))
    book.publish(plan(version=2, published=20, start=25, end=140))

    def snapshot() -> dict[str, object]:
        return {
            "plans": dict(book._plans),          # noqa: SLF001
            "revoked": dict(book._revoked_refs),  # noqa: SLF001
            "signals": dict(book._signals),       # noqa: SLF001
            "meetings": book.meetings_for("s1"),
            "reviews": book.reviews_for("s1"),
            "frozen_match": book.signal_match("s1"),
        }

    before = snapshot()
    outcomes = (
        evaluate(book, active_root(), "CN_A", "600000.SH", at(30)),
        evaluate(book, active_root(status="REVOKED"), "CN_A", "600000.SH", at(30)),
        evaluate(book, None, "CN_A", "600000.SH", at(30)),
        evaluate(book, active_root(), "US", "600000.SH", at(30)),
        evaluate(book, None, "CN_A", "600000.SH",
                 at(30).replace(tzinfo=None)),
    )
    assert [verdict.status for verdict in outcomes] == [
        CURRENT_ELIGIBLE, NOT_ELIGIBLE, FAIL_CLOSED, NOT_ELIGIBLE, FAIL_CLOSED,
    ]
    assert snapshot() == before
    assert book.signal_match("s1") is before["frozen_match"]


# ---------------------------------------------------------------------------
# 10. A verdict is never an execution or notification permission
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [CURRENT_ELIGIBLE, NOT_ELIGIBLE, FAIL_CLOSED])
def test_verdict_is_never_a_permission(status: str) -> None:
    reasons = {
        CURRENT_ELIGIBLE: "CURRENT_PLAN_ACTIVE",
        NOT_ELIGIBLE: "NO_CURRENT_PLAN",
        FAIL_CLOSED: "AUTHORITY_ROOT_MISSING",
    }
    verdict = EligibilityVerdict(status, reasons[status], ("authority:evt-1",))
    assert verdict.trading_permitted is False
    assert verdict.notification_permitted is False
    assert verdict.is_current_eligible is (status == CURRENT_ELIGIBLE)
    assert verdict.is_fail_closed is (status == FAIL_CLOSED)
