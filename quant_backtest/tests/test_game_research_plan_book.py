"""Offline G0/G3 adversarial research-plan and meeting replay tests."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.research_plan_book import (
    MeetingFact,
    PlanRef,
    PlanRevocation,
    ResearchOpinion,
    ResearchPlan,
    ResearchPlanBook,
    ResearchPlanError,
    ResearchReviewFact,
    ResearchSignal,
)


T0 = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def plan(plan_id: str = "p1", version: int = 1, published: int = 0,
         start: int = 0, end: int = 120, **changes: object) -> ResearchPlan:
    values: dict[str, object] = dict(
        ref=PlanRef(plan_id, version), market="CN_A", symbol="600000.SH", side="BUY",
        thesis="synthetic hypothesis", entry_condition="synthetic entry evidence",
        exit_condition="synthetic exit evidence", profile_id="anonymous-conservative",
        profile_version="v1", risk_budget_description="abstract risk template; no account size",
        published_at=at(published), effective_from=at(start), effective_until=at(end),
        approver_id="offline-researcher",
    )
    values.update(changes)
    return ResearchPlan(**values)  # type: ignore[arg-type]


def signal(signal_id: str = "s1", minute: int = 10, **changes: object) -> ResearchSignal:
    values: dict[str, object] = dict(
        signal_id=signal_id, market="CN_A", symbol="600000.SH", side="BUY",
        profile_id="anonymous-conservative", profile_version="v1",
        triggered_at=at(minute), observed_at=at(minute),
    )
    values.update(changes)
    return ResearchSignal(**values)  # type: ignore[arg-type]


def meeting(signal_id: str = "s1", status: str = "HELD", decided: int = 20,
            before: tuple[PlanRef, ...] = (PlanRef("p1", 1),),
            after: PlanRef | None = None, **changes: object) -> MeetingFact:
    values: dict[str, object] = dict(
        meeting_id="m1", signal_id=signal_id, status=status,
        agenda="synthetic event review", decision="dissent retained",
        before_plan_refs=before,
        opinions=(ResearchOpinion("r1", "SUPPORT", "hypothesis holds"),
                  ResearchOpinion("r2", "OPPOSE", "evidence insufficient")),
        decided_at=at(decided), effective_at=at(decided), after_plan_ref=after,
    )
    values.update(changes)
    return MeetingFact(**values)  # type: ignore[arg-type]


def assert_code(code: str, function: object, *args: object,
                **kwargs: object) -> None:
    with pytest.raises(ResearchPlanError, match="^" + code + "$"):
        function(*args, **kwargs)  # type: ignore[operator]


def test_signal_time_frozen_matching_and_meeting_new_version() -> None:
    book = ResearchPlanBook()
    original = book.publish(plan())
    first = book.match_signal(signal())
    assert first.status == "MATCHED" and first.plan_ref == original.ref
    revised = plan(version=2, published=20, start=25, end=140,
                   entry_condition="new synthetic forward-only condition")
    fact = meeting(after=revised.ref, effective_at=at(25))
    assert book.record_meeting(fact, revised) == fact
    assert book.record_meeting(fact, revised) == fact
    assert book.plan(original.ref) == original
    assert book.plan(revised.ref) == revised
    assert book.signal_match("s1") is first
    assert book.match_signal(signal()) is first
    assert book.match_signal(signal("s2", 24)).plan_ref == original.ref
    assert book.match_signal(signal("s3", 25)).plan_ref == revised.ref
    assert book.meetings_for("s1") == (fact,)
    assert book.meetings_for("s1")[0].opinions[1].position == "OPPOSE"
    assert not first.trading_permitted and not first.notification_permitted
    with pytest.raises(FrozenInstanceError):
        original.thesis = "retrospective mutation"  # type: ignore[misc]


@pytest.mark.parametrize("change", [
    {"market": "US"}, {"symbol": "000001.SZ"}, {"side": "SELL"},
    {"profile_id": "anonymous-aggressive"}, {"profile_version": "v2"},
])
def test_market_symbol_side_and_profile_mismatch_never_match(change: dict[str, str]) -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    assert book.match_signal(signal(**change)).status == "NO_VALID_PLAN"


def test_expired_or_revoked_plan_and_no_old_version_fallback() -> None:
    book = ResearchPlanBook()
    book.publish(plan(end=20))
    assert book.match_signal(signal("expired", 20)).status == "NO_VALID_PLAN"
    book.revoke(PlanRevocation("rv1", PlanRef("p1", 1), at(21), "changed hypothesis"))
    assert book.match_signal(signal("revoked", 22)).status == "NO_VALID_PLAN"
    book.publish(plan(version=2, published=23, start=24, end=50))
    book.revoke(PlanRevocation("rv2", PlanRef("p1", 2), at(25), "invalid evidence"))
    assert book.match_signal(signal("later", 26)).status == "NO_VALID_PLAN"
    assert book.signal_match("expired").status == "NO_VALID_PLAN"  # type: ignore[union-attr]
    assert_code("PLAN_ALREADY_REVOKED", book.revoke,
                PlanRevocation("rv3", PlanRef("p1", 2), at(27), "duplicate tombstone"))


def test_delayed_signal_cannot_use_plan_revoked_or_expired_before_observation() -> None:
    revoked = ResearchPlanBook()
    revoked.publish(plan())
    revoked.revoke(PlanRevocation("rv", PlanRef("p1", 1), at(12), "synthetic revoke"))
    delayed = signal("delayed", 10, observed_at=at(13))
    assert revoked.match_signal(delayed).status == "NO_VALID_PLAN"
    expired = ResearchPlanBook()
    expired.publish(plan(end=12))
    assert expired.match_signal(delayed).status == "NO_VALID_PLAN"


def test_latest_version_expired_or_revoked_never_falls_back_to_old_version() -> None:
    expired = ResearchPlanBook()
    expired.publish(plan(end=100))
    expired.publish(plan(version=2, published=10, start=10, end=15))
    assert expired.match_signal(signal("after-expiry", 16)).status == "NO_VALID_PLAN"

    revoked = ResearchPlanBook()
    revoked.publish(plan(end=100))
    revoked.publish(plan(version=2, published=10, start=10, end=40))
    revoked.revoke(PlanRevocation("rv2", PlanRef("p1", 2), at(12),
                                  "latest hypothesis withdrawn"))
    assert revoked.match_signal(signal("after-revoke", 13)).status == "NO_VALID_PLAN"
    assert revoked.plan(PlanRef("p1", 1)) is not None


def test_cross_market_same_symbol_and_lineage_change_fail_closed() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    assert book.match_signal(signal("other-market", market="US")).status == "NO_VALID_PLAN"
    assert_code("PLAN_SCOPE_CHANGED", book.publish,
                plan(version=2, published=11, start=11, market="US"))
    assert book.plan(PlanRef("p1", 2)) is None
    assert_code("SIGNAL_IDENTITY_CONFLICT", book.match_signal,
                signal("other-market", market="HK"))


def test_conflicting_lineages_fail_closed_and_review_cannot_choose_unauthorized_ref() -> None:
    book = ResearchPlanBook()
    book.publish(plan("p1"))
    book.publish(plan("p2", published=1, start=1))
    matched = book.match_signal(signal())
    assert matched.status == "CONFLICT" and matched.plan_ref is None
    assert matched.conflicting_refs == (PlanRef("p1", 1), PlanRef("p2", 1))
    assert_code("REVIEW_PLAN_NOT_VISIBLE_AT_SIGNAL", book.review,
                ResearchReviewFact("r-invalid", "s1", "CONFIRMED", "wrong ref",
                                   at(11), PlanRef("p3", 1)))
    assert book.review(ResearchReviewFact("r1", "s1", "REJECTED", "disagree",
                                          at(11), PlanRef("p1", 1))).decision == "REJECTED"


def test_no_meeting_and_timeout_are_independent_immutable_facts() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    book.match_signal(signal())
    absent = meeting(status="NOT_HELD", decided=12, opinions=(), before=(PlanRef("p1", 1),))
    timeout = meeting(status="TIMED_OUT", decided=16, opinions=(),
                      meeting_id="m2", deadline_at=at(15))
    assert book.record_meeting(absent) == absent
    assert book.record_meeting(timeout) == timeout
    assert book.meetings_for("s1") == (absent, timeout)
    assert_code("ABSENT_MEETING_CANNOT_REVISE", MeetingFact,
                **_kwargs_for_invalid_absent())


def _kwargs_for_invalid_absent() -> dict[str, object]:
    values = dict(meeting(status="NOT_HELD", decided=12, opinions=()).__dict__)
    values["after_plan_ref"] = PlanRef("p1", 2)
    return values


def test_rejection_quick_review_remains_research_only() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    book.match_signal(signal())
    fact = ResearchReviewFact("r1", "s1", "REJECTED", "no evidence", at(11),
                              PlanRef("p1", 1))
    assert book.review(fact) == book.review(fact)
    assert book.reviews_for("s1") == (fact,)
    assert_code("REVIEW_IDENTITY_CONFLICT", book.review,
                replace(fact, decision="CONFIRMED"))
    assert book.signal_match("s1").status == "MATCHED"  # type: ignore[union-attr]


def test_late_plan_cannot_rewrite_signal_or_backfill_history() -> None:
    book = ResearchPlanBook()
    assert book.match_signal(signal()).status == "NO_VALID_PLAN"
    assert_code("HISTORY_BACKFILL_FORBIDDEN", book.publish, plan())
    book.publish(plan(published=20, start=20))
    assert book.match_signal(signal()).status == "NO_VALID_PLAN"
    assert_code("SIGNAL_IDENTITY_CONFLICT", book.match_signal,
                signal(side="SELL"))
    assert_code("HISTORY_BACKFILL_FORBIDDEN", book.record_meeting,
                meeting(decided=19, before=()))
    assert book.match_signal(signal("s2", 20)).status == "MATCHED"


def test_meeting_rejects_backdating_and_does_not_partially_write() -> None:
    book = ResearchPlanBook()
    book.publish(plan())
    book.match_signal(signal())
    revised = plan(version=2, published=20, start=20)
    assert_code("MEETING_PLAN_BACKDATED", book.record_meeting,
                meeting(after=revised.ref, decided=20, effective_at=at(21)), revised)
    assert book.plan(revised.ref) is None and book.meetings_for("s1") == ()
    assert_code("BEFORE_PLAN_REFS_MISMATCH", book.record_meeting,
                meeting(before=(), after=revised.ref), revised)
    assert_code("PLAN_VERSION_NOT_NEXT", book.record_meeting,
                meeting(after=PlanRef("p1", 3)), plan(version=3, published=20, start=20))
    assert book.plan(PlanRef("p1", 3)) is None


def test_utc_and_identity_validation() -> None:
    assert_code("TRIGGERED_AT_UTC_REQUIRED", signal,
                **_kwargs_for_invalid_signal())
    with pytest.raises(ResearchPlanError, match="PUBLISHED_AT_UTC_REQUIRED"):
        plan(published_at=at(0).astimezone(timezone(timedelta(hours=8))))
    with pytest.raises(ResearchPlanError, match="PLAN_WINDOW_INVALID"):
        plan(end=0)
    with pytest.raises(ResearchPlanError, match="PLAN_VERSION_INVALID"):
        PlanRef("p", True)
    with pytest.raises(ResearchPlanError, match="POSITION_INVALID"):
        ResearchOpinion("r", "UNKNOWN", "reason")
    with pytest.raises(ResearchPlanError, match="MARKET_INVALID"):
        plan(market="not a market")
    with pytest.raises(ResearchPlanError, match="MARKET_INVALID"):
        signal(market=None)
    with pytest.raises(ResearchPlanError, match="TIMEOUT_DEADLINE_INVALID"):
        meeting(status="TIMED_OUT", opinions=(), deadline_at=at(21))


def _kwargs_for_invalid_signal() -> dict[str, object]:
    values = dict(signal().__dict__)
    values["triggered_at"] = at(10).replace(tzinfo=None)
    return values
