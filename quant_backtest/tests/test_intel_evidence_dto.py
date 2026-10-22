"""G1-A synthetic projection tests: no real sources, network or persistence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_evidence_dto import (  # noqa: E402
    EvidenceRef, EvidenceSecurityLink, ModelJudgement, ProjectionRejected,
    RightsDecision, SecurityRef, SourceClock, assert_consistent_evidence, page_status,
)

UTC = timezone.utc
T0 = datetime(2026, 10, 21, 8, tzinfo=UTC)


def clock(value=None, source="unknown"):
    return SourceClock(value, source)


def evidence(**changes):
    data = dict(source_id="news", subsource="notice", native_id="record-1", version="1",
                source_ref="notice/record-1", content_hash=None, occurred_at=clock(),
                published_at=clock(T0, "source"), first_seen_at=clock(T0 + timedelta(hours=1), "collector"),
                available_at=clock(T0 + timedelta(hours=2), "consumer"))
    data.update(changes)
    return EvidenceRef(**data)


def test_cross_source_subsource_and_version_identity_are_distinct():
    refs = [evidence(), evidence(source_id="other"), evidence(subsource="report"),
            evidence(version="2", content_hash="old-hash"), evidence(version="3", content_hash="new-hash"),
            evidence(version="4", content_hash="old-hash")]
    assert len({r.key for r in refs}) == 6
    assert refs[-1].content_hash == refs[-3].content_hash and refs[-1].key != refs[-3].key


def test_same_key_conflicting_projection_is_rejected_at_merge_boundary():
    original = evidence(content_hash="hash-a")
    assert_consistent_evidence(original, evidence(content_hash="hash-a"))
    assert_consistent_evidence(original, evidence(native_id="record-2", content_hash="hash-b"))
    with pytest.raises(ProjectionRejected, match="same key"):
        assert_consistent_evidence(original, evidence(content_hash="hash-b"))
    with pytest.raises(ProjectionRejected, match="evidence refs"):
        assert_consistent_evidence(original, object())


def test_missing_clock_fails_closed_for_historical_visibility():
    assert not evidence(published_at=clock()).available_as_of(T0 + timedelta(days=2))
    assert not evidence(first_seen_at=clock()).available_as_of(T0 + timedelta(days=2))
    assert not evidence(available_at=clock()).available_as_of(T0 + timedelta(days=2))
    assert not evidence().available_as_of(T0 + timedelta(hours=1))
    assert evidence().available_as_of(T0 + timedelta(hours=2))
    with pytest.raises(ProjectionRejected, match="as_of"):
        evidence().available_as_of(datetime(2026, 10, 22))


def test_clock_timezone_instant_and_bad_order():
    c = SourceClock(datetime(2026, 10, 21, 16, tzinfo=timezone(timedelta(hours=8))), "publisher")
    assert c.value == T0
    with pytest.raises(ProjectionRejected, match="timezone"):
        SourceClock(datetime(2026, 10, 21), "legacy")
    with pytest.raises(ProjectionRejected, match="before publication"):
        evidence(first_seen_at=clock(T0 - timedelta(seconds=1), "collector"))
    with pytest.raises(ProjectionRejected, match="before first seen"):
        evidence(available_at=clock(T0, "consumer"))


def test_parse_rejects_ambiguous_json_and_missing_clocks():
    data = dict(source_id="news", subsource="notice", native_id="1", version="1", source_ref="src/1",
                content_hash=None, occurred_at={"value": None, "provenance": "unknown"},
                published_at={"value": "2026-10-21T16:00:00+08:00", "provenance": "publisher"},
                first_seen_at={"value": "2026-10-21T09:00:00+00:00", "provenance": "collector"},
                available_at={"value": "2026-10-21T10:00:00+00:00", "provenance": "consumer"})
    assert EvidenceRef.parse(data).available_as_of(T0 + timedelta(hours=2))
    for key, value in [("published_at", {"value": "2026-10-21T08:00:00", "provenance": "publisher"}),
                       ("occurred_at", None), ("surprise", "x")]:
        bad = dict(data, **{key: value})
        with pytest.raises(ProjectionRejected):
            EvidenceRef.parse(bad)
    for missing in ("content_hash", "occurred_at", "first_seen_at"):
        bad = dict(data)
        bad.pop(missing)
        with pytest.raises(ProjectionRejected, match="missing evidence"):
            EvidenceRef.parse(bad)
    with pytest.raises(ProjectionRejected, match="value and provenance"):
        SourceClock.parse({"provenance": "unknown"})


def test_security_and_relation_keep_candidate_separate_from_fact():
    sec = SecurityRef("605100.SH")
    unknown = clock()
    guess = EvidenceSecurityLink(evidence(), sec, "mentions", "title:name_match", "candidate", unknown, unknown)
    assert guess.state == "candidate"
    with pytest.raises(ProjectionRejected, match="trusted source verifier"):
        EvidenceSecurityLink(evidence(), sec, "mentions", "news.stocks:exact_code", "fact", unknown, unknown)
    with pytest.raises(ProjectionRejected, match="trusted source verifier"):
        EvidenceSecurityLink(evidence(source_id="other"), sec, "mentions", "news.stocks:exact_code", "fact", unknown, unknown)
    with pytest.raises(ProjectionRejected, match="code"):
        SecurityRef("605100")
    with pytest.raises(ProjectionRejected, match="state"):
        EvidenceSecurityLink(evidence(), sec, "mentions", "title:name_match", "proved_causality", unknown, unknown)
    with pytest.raises(ProjectionRejected, match="validity"):
        EvidenceSecurityLink(evidence(), sec, "mentions", "news.stocks:exact_code", "candidate",
                             clock(T0, "event"), clock(T0 - timedelta(days=1), "event"))


def test_judgement_never_becomes_factual_edge_or_probability_of_return():
    label = ModelJudgement("bull", 0.8, "model-v1")
    assert label.confidence == 0.8 and not isinstance(label, EvidenceSecurityLink)
    for value in (True, float("nan"), 1.1):
        with pytest.raises(ProjectionRejected, match="judgement"):
            ModelJudgement("bull", value, "model-v1")


def test_rights_reference_is_not_an_authorization_token():
    assert RightsDecision(None, "unknown").state == "unknown"
    with pytest.raises(ProjectionRejected, match="reference"):
        RightsDecision(None, "reviewed")
    with pytest.raises(ProjectionRejected, match="state"):
        RightsDecision("permission", "approved")


def test_independent_review_rejects_unknown_clock_and_untrusted_fact():
    with pytest.raises(ProjectionRejected, match="unknown provenance"):
        SourceClock(T0, "unknown")
    with pytest.raises(ProjectionRejected, match="unknown provenance"):
        SourceClock(None, "collector")
    with pytest.raises(ProjectionRejected, match="trusted source verifier"):
        EvidenceSecurityLink(evidence(), SecurityRef("605100.SH"), "mentions",
                             "title:name_match", "fact", clock(), clock())
    with pytest.raises(ProjectionRejected, match="trusted source verifier"):
        EvidenceSecurityLink(evidence(), SecurityRef("605100.SH"), "DRIVE",
                             "news.stocks:exact_code", "fact", clock(), clock())
    with pytest.raises(ProjectionRejected, match="state"):
        EvidenceSecurityLink(evidence(), SecurityRef("605100.SH"), "mentions",
                             "title:name_match", [], clock(), clock())


def test_relation_respects_validity_and_its_own_availability():
    args = (evidence(), SecurityRef("605100.SH"), "mentions",
            "news.stocks:exact_code", "candidate")
    link = EvidenceSecurityLink(*args, clock(T0 + timedelta(days=1), "event"), clock(),
                                clock(T0 + timedelta(days=1, hours=1), "consumer"))
    assert not link.available_as_of(T0 + timedelta(hours=2))
    assert not link.available_as_of(T0 + timedelta(days=1))
    assert link.available_as_of(T0 + timedelta(days=2))
    assert not link.available_as_of(T0 + timedelta(hours=1))
    assert not EvidenceSecurityLink(*args, clock(), clock()).available_as_of(T0 + timedelta(days=2))
    assert not EvidenceSecurityLink(*args, clock(), clock(), clock()).available_as_of(T0 + timedelta(days=2))
    with pytest.raises(ProjectionRejected, match="relation availability"):
        EvidenceSecurityLink(*args, clock(), clock(), "2026-10-22")
    unavailable_evidence = EvidenceSecurityLink(
        evidence(published_at=clock()), SecurityRef("605100.SH"), "mentions",
        "title:name_match", "candidate", clock(), clock(), clock(T0, "consumer"))
    for row in (link, unavailable_evidence, EvidenceSecurityLink(*args, clock(), clock())):
        with pytest.raises(ProjectionRejected, match="as_of"):
            row.available_as_of(datetime(2026, 10, 22))
    later_relation = EvidenceSecurityLink(*args, clock(), clock(),
                                          clock(T0 + timedelta(days=1), "consumer"))
    assert not later_relation.available_as_of(T0 + timedelta(hours=2))
    assert later_relation.available_as_of(T0 + timedelta(days=1))


def test_publication_visibility_is_distinct_from_event_occurrence():
    future_event = evidence(occurred_at=clock(T0 + timedelta(days=1), "event"))
    assert future_event.available_as_of(T0 + timedelta(hours=2))
    late_disclosure = evidence(published_at=clock(T0 + timedelta(days=1), "source"),
                               first_seen_at=clock(T0 + timedelta(days=1, hours=1), "collector"),
                               available_at=clock(T0 + timedelta(days=1, hours=2), "consumer"),
                               occurred_at=clock(T0, "event"))
    assert not late_disclosure.available_as_of(T0 + timedelta(hours=2))
    assert late_disclosure.available_as_of(T0 + timedelta(days=2))


def test_page_counts_and_partial_failures_are_explicit():
    assert page_status(12, 3, partial=True, errors=("source unavailable",)) == {
        "total": 12, "shown": 3, "truncated": True, "partial": True,
        "errors": ["source unavailable"], "coverage": "loaded_authorized_scope_only"}
    assert page_status(0, 0, partial=False)["truncated"] is False
    for args in ((1, 2), (-1, 0), (True, 0)):
        with pytest.raises(ProjectionRejected, match="counts"):
            page_status(*args, partial=False)
    with pytest.raises(ProjectionRejected, match="errors require partial"):
        page_status(1, 1, partial=False, errors=("source unavailable",))
