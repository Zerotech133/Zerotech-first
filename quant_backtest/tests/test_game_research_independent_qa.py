"""Independent offline adversarial checks for the research-only plan book."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector import dual_gate
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

BASE = datetime(2026, 10, 20, tzinfo=timezone.utc)


def at(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


def plan(*, plan_id: str = "research", version: int = 1,
         published: int = 0, start: int = 0, end: int = 100,
         profile: str = "conservative") -> ResearchPlan:
    return ResearchPlan(
        ref=PlanRef(plan_id, version), market="CN_A", symbol="600000.SH", side="BUY",
        thesis="synthetic hypothesis", entry_condition="synthetic entry",
        exit_condition="synthetic exit", profile_id=profile,
        profile_version="v1", risk_budget_description="anonymous template",
        published_at=at(published), effective_from=at(start),
        effective_until=at(end), approver_id="offline-researcher",
    )


def signal(signal_id: str, *, triggered: int, observed: int | None = None,
           profile: str = "conservative") -> ResearchSignal:
    return ResearchSignal(
        signal_id=signal_id, market="CN_A", symbol="600000.SH", side="BUY",
        profile_id=profile, profile_version="v1",
        triggered_at=at(triggered),
        observed_at=at(triggered if observed is None else observed),
    )


def meeting(signal_id: str, *, decided: int, status: str = "HELD",
            before: tuple[PlanRef, ...] = (PlanRef("research", 1),),
            after: PlanRef | None = None,
            deadline: int | None = None) -> MeetingFact:
    return MeetingFact(
        meeting_id="meeting-1", signal_id=signal_id, status=status,
        agenda="synthetic signal review", decision="retain dissent",
        before_plan_refs=before,
        opinions=(ResearchOpinion("analyst-a", "SUPPORT", "reason a"),)
        if status == "HELD" else (), decided_at=at(decided),
        effective_at=at(decided), after_plan_ref=after,
        deadline_at=at(deadline) if deadline is not None else None,
    )


class TestSignalTemporalIdentity:
    @pytest.mark.parametrize(
        ("published", "start", "triggered", "observed", "expected"),
        [
            (10, 10, 10, 10, "MATCHED"),
            (11, 11, 10, 12, "NO_VALID_PLAN"),
            (5, 11, 10, 12, "NO_VALID_PLAN"),
            (5, 10, 10, 12, "MATCHED"),
            (5, 10, 10, 20, "NO_VALID_PLAN"),
        ],
    )
    def test_publication_effective_and_expiry_are_not_backfilled(
        self, published: int, start: int, triggered: int,
        observed: int, expected: str,
    ) -> None:
        book = ResearchPlanBook()
        book.publish(plan(published=published, start=start, end=20))
        assert book.match_signal(signal("s1", triggered=triggered,
                                        observed=observed)).status == expected

    @pytest.mark.parametrize(("revoked", "observed", "expected"), [
        (11, 11, "NO_VALID_PLAN"),
        (11, 12, "NO_VALID_PLAN"),
        (12, 11, "MATCHED"),
    ])
    def test_revocation_is_effective_at_observation_not_market_event(
        self, revoked: int, observed: int, expected: str,
    ) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        if revoked <= observed:
            book.revoke(PlanRevocation("rv", PlanRef("research", 1),
                                       at(revoked), "synthetic tombstone"))
        match = book.match_signal(signal("delayed", triggered=10,
                                         observed=observed))
        assert match.status == expected
        if revoked > observed:
            book.revoke(PlanRevocation("rv", PlanRef("research", 1),
                                       at(revoked), "synthetic tombstone"))
            assert book.signal_match("delayed") is match

    def test_same_signal_id_changed_observation_is_identity_conflict(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        first = book.match_signal(signal("same-id", triggered=10))
        with pytest.raises(ResearchPlanError, match="^SIGNAL_IDENTITY_CONFLICT$"):
            book.match_signal(signal("same-id", triggered=10, observed=11))
        assert book.signal_match("same-id") is first

    @pytest.mark.parametrize("invalid_market", ["", "bad market", None])
    def test_invalid_signal_market_rejects_bad_values(self, invalid_market: object) -> None:
        with pytest.raises(ResearchPlanError, match="^MARKET_INVALID$"):
            replace(signal("invalid", triggered=10), market=invalid_market)

    def test_same_symbol_other_market_cannot_match(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        foreign_market = replace(signal("foreign", triggered=10), market="HK_STOCK")
        assert book.match_signal(foreign_market).status == "NO_VALID_PLAN"

    def test_anonymous_profile_isolation_is_bidirectional(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan(profile="conservative"))
        book.publish(plan(plan_id="aggressive", published=1, start=1,
                          profile="aggressive"))
        conservative = book.match_signal(signal("c", triggered=10))
        aggressive = book.match_signal(signal("a", triggered=10,
                                              profile="aggressive"))
        assert conservative.plan_ref == PlanRef("research", 1)
        assert aggressive.plan_ref == PlanRef("aggressive", 1)
        assert conservative.status == aggressive.status == "MATCHED"
        assert not conservative.trading_permitted
        assert not aggressive.notification_permitted


class TestMeetingAndAuthorityBoundary:
    def test_meeting_plan_is_forward_effective_and_original_match_frozen(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        original = book.match_signal(signal("first", triggered=10))
        updated = plan(version=2, published=20, start=25)
        fact = replace(meeting("first", decided=20, after=updated.ref),
                       effective_at=at(25))
        book.record_meeting(fact, updated)
        assert book.match_signal(signal("before", triggered=24)).plan_ref == original.plan_ref
        assert book.match_signal(signal("after", triggered=25)).plan_ref == updated.ref
        assert book.signal_match("first") is original

    def test_absent_meeting_cannot_publish_plan_and_timeout_is_auditable(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        book.match_signal(signal("first", triggered=10))
        absent = meeting("first", decided=11, status="NOT_HELD")
        timed_out = replace(meeting("first", decided=15, status="TIMED_OUT",
                                    deadline=14), meeting_id="meeting-2")
        assert book.record_meeting(absent) is absent
        assert book.record_meeting(timed_out) is timed_out
        assert book.meetings_for("first") == (absent, timed_out)
        new_plan = plan(version=2, published=16, start=16)
        with pytest.raises(ResearchPlanError, match="^ABSENT_MEETING_CANNOT_REVISE$"):
            replace(timed_out, after_plan_ref=new_plan.ref)
        with pytest.raises(ResearchPlanError, match="^MEETING_PLAN_BINDING_INVALID$"):
            book.record_meeting(replace(meeting("first", decided=16),
                                        meeting_id="meeting-3"), new_plan)
        assert book.plan(new_plan.ref) is None

    def test_research_review_does_not_implement_the_dual_gate_review(self) -> None:
        book = ResearchPlanBook()
        book.publish(plan())
        match = book.match_signal(signal("first", triggered=10))
        quick_review = ResearchReviewFact(
            review_id="quick-review", signal_id="first", decision="CONFIRMED",
            reason="synthetic opinion", recorded_at=at(11), plan_ref=match.plan_ref,
        )
        assert book.review(quick_review) is quick_review
        assert book.reviews_for("first") == (quick_review,)
        assert not isinstance(quick_review, dual_gate.ResearchReview)
        assert match.trading_permitted is False
        assert match.notification_permitted is False
        assert not hasattr(quick_review, "envelope_hash")
        assert not hasattr(quick_review, "evidence_version")
        assert not hasattr(quick_review, "reviewer_external_identity")
