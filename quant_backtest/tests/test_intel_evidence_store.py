"""K-MDC-R03 tests: four-state ledger, idempotency, crash replay, watermark.

Synthetic-only: tmp_path persistence, no network, no real sources.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_evidence_store import (  # noqa: E402
    ACCEPTED, EvidenceItem, EvidenceStore, OFFERED, PERSISTED, StoreRejected, VISIBLE,
)


def item(n: int = 1, *, src: str = "s1", ref: str | None = None, rcpt: str | None = None) -> EvidenceItem:
    return EvidenceItem("s1" if src == "s1" else src, "post", f"record-{n}", f"h{n}",
                        ref or f"s1/record-{n}", rcpt or f"r{n}")


def test_four_state_order_and_jump_rejection(tmp_path):
    store = EvidenceStore(tmp_path)
    it = item(1)
    assert store.offer(it)["state"] == OFFERED
    with pytest.raises(StoreRejected, match="STATE_INVALID"):
        store.persist(it)  # offered -> persisted jump forbidden
    assert store.accept(it)["state"] == ACCEPTED
    with pytest.raises(StoreRejected, match="EVIDENCE_UNKNOWN"):
        store.accept(item(2))  # unknown item
    assert store.persist(it)["state"] == PERSISTED
    with pytest.raises(StoreRejected, match="STATE_INVALID"):
        store.accept(it)  # backwards forbidden
    assert store.mark_visible(it, "consumer-A", "rcpt-1")["state"] == VISIBLE
    assert store.counts() == {OFFERED: 0, ACCEPTED: 0, PERSISTED: 0, VISIBLE: 1}


def test_idempotent_reoffer_and_provenance_conflict(tmp_path):
    store = EvidenceStore(tmp_path)
    first = store.offer(item(1))
    assert store.offer(item(1)) == first  # full-provenance idempotent
    drifted_ref = item(1, ref="s1/record-1-moved")
    with pytest.raises(StoreRejected, match="PROVENANCE_CONFLICT"):
        store.offer(drifted_ref)  # same content, moved pointer
    drifted_rcpt = item(1, rcpt="r9")
    with pytest.raises(StoreRejected, match="PROVENANCE_CONFLICT"):
        store.offer(drifted_rcpt)  # same content, new receipt
    assert store.counts()[OFFERED] == 1  # conflicts created nothing


def test_buffer_full_counts_drop_and_recovers_after_drain(tmp_path):
    store = EvidenceStore(tmp_path, capacity=2)
    store.offer(item(1)); store.offer(item(2))
    with pytest.raises(StoreRejected, match="OFFER_BUFFER_FULL"):
        store.offer(item(3))
    assert store.drop_count == 1
    # drain one through the pipeline, buffer frees
    it = item(1)
    store.accept(it); store.persist(it)
    third = store.offer(item(3))
    assert third["state"] == OFFERED and store.drop_count == 1  # drop_count is cumulative offers-rejected


def test_write_after_persist_crash_keeps_persisted_and_no_duplicate(tmp_path):
    store = EvidenceStore(tmp_path)
    it = item(7)
    store.offer(it); store.accept(it)

    def boom():
        raise RuntimeError("downstream handler exploded after durable write")

    with pytest.raises(RuntimeError):
        store.persist(it, after_write=boom)
    # crash after durable write: replay keeps persisted, exactly one record
    revived = store.replay()
    assert revived.state_of(it) == PERSISTED
    assert sum(revived.counts().values()) == 1
    # re-persist after recovery is idempotent (no second record)
    assert revived.persist(it)["state"] == PERSISTED
    assert sum(revived.counts().values()) == 1


def test_crash_replay_preserves_states_and_visibility_receipts(tmp_path):
    store = EvidenceStore(tmp_path)
    a, b, c = item(1), item(2), item(3)
    store.offer(a); store.accept(a); store.persist(a); store.mark_visible(a, "os", "rcpt-a")
    store.offer(b); store.accept(b)
    store.offer(c)
    revived = store.replay()
    assert revived.counts() == {OFFERED: 1, ACCEPTED: 1, PERSISTED: 0, VISIBLE: 1}
    assert revived._record_of(a.key_json)["visibility"] == {"os": "rcpt-a"}
    assert revived.drop_count == 0
    # in-memory tail after restart: persisted items no longer occupy buffer
    assert len(revived._inflight) == 2


def test_watermark_freezes_on_hole_and_advances_when_filled(tmp_path):
    store = EvidenceStore(tmp_path)
    a, b, c = item(1), item(2), item(3)
    store.offer(a); store.accept(a); store.persist(a)
    store.offer(b)                       # hole: offered, not confirmed
    store.offer(c); store.accept(c); store.persist(c)
    assert store.high_watermark() == 1   # c persisted but b blocks the run
    store.accept(b); store.persist(b)
    assert store.high_watermark() == 3   # contiguous run complete


def test_visibility_receipt_and_consumer_required(tmp_path):
    store = EvidenceStore(tmp_path)
    it = item(5)
    store.offer(it); store.accept(it); store.persist(it)
    with pytest.raises(StoreRejected, match="VISIBILITY_RECEIPT_REQUIRED"):
        store.mark_visible(it, "consumer-A", " ")
    with pytest.raises(StoreRejected, match="consumer_id required"):
        store.mark_visible(it, "", "rcpt")
    with pytest.raises(StoreRejected, match="EVIDENCE_UNKNOWN"):
        store.mark_visible(item(9), "consumer-A", "rcpt")  # unknown item rejected


@pytest.mark.parametrize("state", [OFFERED, ACCEPTED])
def test_rejected_visibility_jump_does_not_poison_receipts(tmp_path, state):
    store = EvidenceStore(tmp_path)
    it = item(11)
    store.offer(it)
    if state == ACCEPTED:
        store.accept(it)
    with pytest.raises(StoreRejected, match="STATE_INVALID"):
        store.mark_visible(it, "os", "fake-receipt")
    assert store.state_of(it) == state
    assert store._record_of(it.key_json)["visibility"] == {}
    revived = store.replay()
    assert revived.state_of(it) == state
    assert revived._record_of(it.key_json)["visibility"] == {}
    # A subsequent legitimate transition must not carry the rejected receipt.
    if state == OFFERED:
        store.accept(it)
    store.persist(it)
    assert store.mark_visible(it, "os", "real-receipt")["visibility"] == {"os": "real-receipt"}


def test_bad_items_and_capacity_rejected(tmp_path):
    with pytest.raises(StoreRejected, match="provenance"):
        EvidenceItem("s1", "post", "x", "h", "", "r")
    with pytest.raises(StoreRejected, match="capacity"):
        EvidenceStore(tmp_path / "cap", capacity=0)
