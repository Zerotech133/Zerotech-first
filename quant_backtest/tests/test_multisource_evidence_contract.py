"""R02 synthetic-only contract tests; no external requests or databases."""
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multisource_evidence_contract import (  # noqa: E402
    ContractDenied, EvidenceCandidate, EvidenceBook, RightsScope,
)

NOW = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)


def scope(**changes):
    fields = dict(source_id="source-A", content_type="post", licensing_party="owner",
                  evidence_ref="permission-1", owner_id="owner", permitted_actions=frozenset({"index", "derive"}),
                  permitted_use="research", valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                  valid_until=datetime(2026, 12, 31, tzinfo=timezone.utc),
                  retention_rule="remove on revocation", revocation_rule="stop and remove",
                  stop_condition="permission revoked", review_state="approved")
    fields.update(changes)
    return RightsScope(**fields)


def candidate(**changes):
    fields = dict(source_id="source-A", content_type="post", source_record_id="record-1",
                  source_ref="source-A/record-1", content_hash="h1", parser_version="v1",
                  run_id="run-1", receipt_id="receipt-1")
    fields.update(changes)
    return EvidenceCandidate(**fields)


def test_missing_licence_and_login_do_not_authorize():
    book = EvidenceBook()
    for bad in [scope(evidence_ref=""), scope(review_state="unknown"),
                scope(permitted_actions=frozenset({"index"})), scope(permitted_use="other"),
                scope(valid_until=datetime(2026, 1, 1, tzinfo=timezone.utc))]:
        with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
            book.accept(bad, candidate(), action="derive", use="research", observed_at=NOW)
    assert book.versions("source-A", "post", "record-1") == ()


def test_different_sources_and_revisions_are_distinct():
    book = EvidenceBook()
    first = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    other = book.accept(scope(source_id="source-B"), candidate(source_id="source-B"),
                        action="index", use="research", observed_at=NOW)
    second = book.accept(scope(), candidate(content_hash="h2"),
                         action="index", use="research", observed_at=NOW)
    assert first.version == 1 and other.version == 1 and second.version == 2
    assert len(book.versions("source-A", "post", "record-1")) == 2
    assert len(book.versions("source-B", "post", "record-1")) == 1
    assert book.accept(scope(), candidate(content_hash="h2"),
                       action="index", use="research", observed_at=NOW) == second


def test_no_inferred_first_seen_or_consumer_availability():
    book = EvidenceBook()
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.accept(scope(), candidate(source_published_at=NOW),
                    action="index", use="research", observed_at=None)
    item = book.accept(scope(), candidate(source_published_at=NOW),
                       action="index", use="research", observed_at=NOW)
    assert item.first_seen_at == NOW
    with pytest.raises(ContractDenied, match="PIT_TIME_UNKNOWN"):
        book.as_of(item, "consumer-A", NOW, action="index", use="research")
    with pytest.raises(ContractDenied, match="VISIBILITY_RECEIPT_REQUIRED"):
        book.mark_visible(item, "consumer-A", NOW, "", persisted=True, action="index", use="research")
    with pytest.raises(ContractDenied, match="PERSISTENCE_REQUIRED"):
        book.mark_visible(item, "consumer-A", NOW, "read-1", persisted=False, action="index", use="research")
    book.observe(item, NOW, "observation-1")
    book.mark_visible(item, "consumer-A", NOW, "read-1", persisted=True, action="index", use="research")
    assert book.as_of(item, "consumer-A", NOW, action="index", use="research")
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.as_of(item, "consumer-A", NOW, action="derive", use="research")
    with pytest.raises(ContractDenied, match="PIT_TIME_UNKNOWN"):
        book.as_of(item, "consumer-B", NOW, action="index", use="research")


def test_revocation_blocks_new_and_existing_visibility():
    book = EvidenceBook()
    item = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    book.mark_visible(item, "consumer-A", NOW, "visible-1", persisted=True, action="index", use="research")
    book.revoke("source-A", "post", "permission-1")
    assert not book.as_of(item, "consumer-A", NOW, action="index", use="research")
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.accept(scope(), candidate(content_hash="h2"), action="index", use="research", observed_at=NOW)


def test_reject_timezone_naive_and_cross_source_receipts():
    book = EvidenceBook()
    with pytest.raises(ContractDenied, match="TIME_INVALID"):
        book.accept(scope(), candidate(), action="index", use="research", observed_at=datetime(2026, 10, 22))
    item = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    with pytest.raises(ContractDenied, match="EVIDENCE_UNKNOWN"):
        book.observe(candidate(source_id="source-B"), NOW, "observation-1")
    other_book = EvidenceBook()
    foreign = other_book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    with pytest.raises(ContractDenied, match="EVIDENCE_UNKNOWN"):
        book.mark_visible(foreign, "consumer-A", NOW, "visible-2", persisted=True, action="index", use="research")
    with pytest.raises(ContractDenied, match="TIME_INVALID"):
        book.mark_visible(item, "consumer-A", datetime(2026, 10, 22), "visible-1", persisted=True, action="index", use="research")


def test_scope_requires_valid_window_and_actions():
    book = EvidenceBook()
    for bad in [scope(valid_from=datetime(2026, 1, 1)),
                scope(permitted_actions="index"),
                scope(permitted_actions=frozenset({"index", ""}))]:
        with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
            book.accept(bad, candidate(), action="index", use="research", observed_at=NOW)


def test_dst_fold_uses_instant_not_wall_clock():
    class FoldZone(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=-5 if dt.fold else -4)

        def dst(self, dt):
            return timedelta(0)

    zone = FoldZone()
    first = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    earlier = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    book = EvidenceBook()
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.accept(scope(valid_from=first, valid_until=datetime(2026, 11, 2, tzinfo=zone)),
                    candidate(), action="index", use="research", observed_at=earlier)
    item = book.accept(scope(valid_from=first, valid_until=datetime(2026, 11, 2, tzinfo=zone)),
                       candidate(), action="index", use="research", observed_at=first)
    book.mark_visible(item, "consumer-A", first, "visible-1", persisted=True, action="index", use="research")
    assert not book.as_of(item, "consumer-A", earlier, action="index", use="research")


def test_expiry_and_action_gate_on_visibility():
    book = EvidenceBook()
    item = book.accept(scope(permitted_actions=frozenset({"index"})), candidate(),
                       action="index", use="research", observed_at=NOW)
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.mark_visible(item, "consumer-A", NOW, "visible-1", persisted=True, action="derive", use="research")
    late = datetime(2027, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.mark_visible(item, "consumer-A", late, "visible-2", persisted=True, action="index", use="research")
    book.mark_visible(item, "consumer-A", NOW, "visible-3", persisted=True, action="index", use="research")
    assert not book.as_of(item, "consumer-A", late, action="index", use="research")


def test_visibility_cannot_precede_observation():
    book = EvidenceBook()
    item = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    previous = datetime(2026, 10, 21, 23, tzinfo=timezone.utc)
    with pytest.raises(ContractDenied, match="TIME_INVALID"):
        book.mark_visible(item, "consumer-A", previous, "visible-1", persisted=True, action="index", use="research")


def test_hash_rollback_and_reauthorization_append_versions():
    book = EvidenceBook()
    one = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    two = book.accept(scope(), candidate(content_hash="h2"), action="index", use="research", observed_at=NOW)
    three = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    book.revoke("source-A", "post", "permission-1")
    four = book.accept(scope(evidence_ref="permission-2"), candidate(),
                       action="index", use="research", observed_at=NOW)
    assert [item.version for item in (one, two, three, four)] == [1, 2, 3, 4]
    assert four.rights_ref == "permission-2"


def test_future_publication_cannot_enter_pit_early():
    book = EvidenceBook()
    tomorrow = datetime(2026, 10, 23, tzinfo=timezone.utc)
    with pytest.raises(ContractDenied, match="TIME_INVALID"):
        book.accept(scope(), candidate(source_published_at=tomorrow),
                    action="index", use="research", observed_at=NOW)


def test_same_reference_cannot_silently_narrow_rights():
    book = EvidenceBook()
    original = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    book.mark_visible(original, "consumer-A", NOW, "visible-1", persisted=True,
                      action="index", use="research")
    with pytest.raises(ContractDenied, match="BLOCK_SOURCE_RIGHTS"):
        book.accept(scope(permitted_actions=frozenset({"index"})), candidate(),
                    action="index", use="research", observed_at=NOW)
    assert len(book.versions("source-A", "post", "record-1")) == 1


def test_candidate_requires_stable_identity_and_hash():
    book = EvidenceBook()
    for bad in [candidate(source_record_id="", receipt_id=""), candidate(content_hash=""), candidate(source_ref="")]:
        with pytest.raises(ContractDenied, match="EVIDENCE_INVALID"):
            book.accept(scope(), bad, action="index", use="research", observed_at=NOW)


def test_a6_same_hash_changed_provenance_is_rejected_not_silently_reused():
    # A6 regression: same key + same hash + same rights, but source_ref or receipt_id
    # drifted. Old code silently returned the stored version, hiding the lineage change.
    book = EvidenceBook()
    first = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    assert first.source_ref == "source-A/record-1" and first.receipt_id == "receipt-1"
    # identical re-submission stays idempotent
    assert book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW) is first
    # moved pointer under same hash -> fail closed
    with pytest.raises(ContractDenied, match="PROVENANCE_CONFLICT"):
        book.accept(scope(), candidate(source_ref="source-A/record-1-moved"),
                    action="index", use="research", observed_at=NOW)
    # new collection receipt under same hash -> fail closed
    with pytest.raises(ContractDenied, match="PROVENANCE_CONFLICT"):
        book.accept(scope(), candidate(receipt_id="receipt-2"),
                    action="index", use="research", observed_at=NOW)
    # no phantom versions were created by the rejected attempts
    assert len(book.versions("source-A", "post", "record-1")) == 1
    # a real content change still versions normally and carries new provenance
    second = book.accept(scope(), candidate(content_hash="h2", source_ref="source-A/record-1@v2",
                                            receipt_id="receipt-3"),
                         action="index", use="research", observed_at=NOW)
    assert second.version == 2 and second.source_ref.endswith("@v2") and second.receipt_id == "receipt-3"


def test_a6_provenance_fields_are_auditable_on_every_version():
    book = EvidenceBook()
    one = book.accept(scope(), candidate(), action="index", use="research", observed_at=NOW)
    two = book.accept(scope(), candidate(content_hash="h2", source_ref="r2", receipt_id="rc2"),
                      action="index", use="research", observed_at=NOW)
    for item, ref, rcpt in ((one, "source-A/record-1", "receipt-1"), (two, "r2", "rc2")):
        assert item.source_ref == ref and item.receipt_id == rcpt

