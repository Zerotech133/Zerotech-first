"""W-Q3 lease-expired suspended intent recovery tests.

Covers the W-Q1 follow-up ("分发账租约过期悬挂 intent 无自动回收"): the
batch recovery entry, its append-only JSONL audit log, idempotency by
intent id, and zero behavior change on unexpired or completed paths.

Offline only; no network, no execution semantics. The only file touched is
the explicitly injected JSONL audit log under test.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.dispatch_ledger import (
    ACCEPTED_VERIFIED,
    CANCELLED,
    CLAIMED,
    DEFINITELY_REJECTED,
    DispatchIntent,
    DispatchLedger,
    DispatchLedgerError,
    READY,
    UNKNOWN,
)
from quant_backtest.quant_sector.lease_recovery import (
    REASON_LEASE_EXPIRED,
    RECORD_KIND,
    LeaseRecoveryError,
    LeaseRecoveryLog,
    LeaseRecoveryRecord,
    recover_expired_intents,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamReceipt,
    canonical_bytes,
    utc_text,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
ACTOR = "quant-ops-1"


def make_report(**changes) -> QuantUpstreamReport:
    fields = dict(report_id="rep1", report_kind="ANOMALY", event_id="ev1",
                  evidence_hash=H, evidence_version=1,
                  sender_agent_id="watch-agent-1",
                  receiver_agent_id="quant-sector-agent",
                  created_at=T)
    fields.update(changes)
    return QuantUpstreamReport(**fields)


def fixture_inbox() -> tuple:
    inbox = UpstreamInbox()
    receipt = inbox.accept(make_report(), now=T)
    return inbox, receipt


def make_ledger(route: str, inbox: UpstreamInbox) -> DispatchLedger:
    return DispatchLedger(route, inbox_lookup=inbox.is_active)


def make_intent(route: str, receipt: UpstreamReceipt, *,
                intent_id: str = "int1", target_ref: str = "feishu:chan1",
                created_at: datetime = T) -> DispatchIntent:
    return DispatchIntent(intent_id=intent_id, receipt_id=receipt.receipt_id,
                          route=route, target_ref=target_ref,
                          created_at=created_at)


def plan(ledger: DispatchLedger, receipt: UpstreamReceipt, **kwargs):
    intent = kwargs.pop("intent") if "intent" in kwargs else make_intent(
        ledger.route, receipt, **kwargs)
    return ledger.plan(intent, receipt_view=receipt, now=T), intent


def claim_and_expire(ledger: DispatchLedger, receipt: UpstreamReceipt, *,
                     intent_id: str, target_ref: str = "feishu:chan1"):
    """Plan + claim with a one-minute lease and let the lease expire."""
    ledger.plan(make_intent(ledger.route, receipt, intent_id=intent_id,
                            target_ref=target_ref),
                receipt_view=receipt, now=T)
    return ledger.claim(intent_id, now=T, lease_until=T + timedelta(minutes=1))


def test_expired_suspended_intent_is_recovered_with_full_record():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    # The suspended form: CLAIMED with a fully elapsed lease.
    assert ledger.status("int1") == CLAIMED
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim("int1", now=T + timedelta(minutes=2),
                     lease_until=T + timedelta(minutes=5))

    log = LeaseRecoveryLog()
    now = T + timedelta(minutes=3)
    report = recover_expired_intents(ledger, log, now=now,
                                     recovered_by=ACTOR)

    assert report.scanned == 1
    assert report.recovered == ("int1",)
    assert report.already_recovered == ()
    # Reclaimed through the sanctioned operator path: back to READY.
    assert ledger.status("int1") == READY
    # Exactly one record, with every required field populated.
    assert len(log.records) == 1
    record = log.records[0]
    assert record.record_kind == RECORD_KIND
    assert record.intent_id == "int1"
    assert record.route == "FEISHU_INFO"
    assert record.receipt_id == receipt.receipt_id
    assert record.attempt_no == 1
    assert record.lease_until == T + timedelta(minutes=1)
    assert record.recovered_at == now
    assert record.reason_code == REASON_LEASE_EXPIRED
    assert record.recovered_by == ACTOR
    # Append-only on the ledger side: history and epoch are untouched and
    # the expired attempt record survives as CLAIMED.
    history = ledger.get_attempts("int1")
    assert len(history) == 1
    assert history[-1].outcome == CLAIMED
    assert ledger.ledger_epoch == 0


def test_unexpired_and_non_suspended_states_are_never_recovered():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    # Live lease: claimed but not yet expired.
    plan(ledger, receipt, intent_id="int-live")
    ledger.claim("int-live", now=T, lease_until=T + timedelta(minutes=5))
    # READY: planned, never claimed.
    plan(ledger, receipt, intent_id="int-ready")
    # Terminal UNKNOWN.
    claim_and_expire(ledger, receipt, intent_id="int-unknown")
    ledger.finish(ledger.get_attempts("int-unknown")[-1], UNKNOWN,
                  now=T + timedelta(minutes=2))
    # Terminal ACCEPTED_VERIFIED inside the lease window.
    plan(ledger, receipt, intent_id="int-done")
    attempt = ledger.claim("int-done", now=T,
                           lease_until=T + timedelta(minutes=5))
    ledger.finish(attempt, ACCEPTED_VERIFIED, now=T + timedelta(seconds=30),
                  external_message_id="msg-ok",
                  observed_at=T + timedelta(seconds=20))
    # CANCELLED.
    plan(ledger, receipt, intent_id="int-cancel")
    ledger.cancel("int-cancel", now=T + timedelta(seconds=1))
    # Revoked: forced terminal UNKNOWN with epoch bump.
    plan(ledger, receipt, intent_id="int-revoked")
    ledger.claim("int-revoked", now=T, lease_until=T + timedelta(minutes=5))
    ledger.revoke("int-revoked", now=T + timedelta(seconds=5))
    # Boundary: now == lease_until is NOT expired (strictly greater required).
    plan(ledger, receipt, intent_id="int-boundary")
    ledger.claim("int-boundary", now=T, lease_until=T + timedelta(minutes=1))

    log = LeaseRecoveryLog()
    now = T + timedelta(minutes=1)
    report = recover_expired_intents(ledger, log, now=now,
                                     recovered_by=ACTOR)

    assert report.scanned == 7
    assert report.recovered == ()
    assert report.already_recovered == ()
    assert log.records == ()
    # Every status is exactly what it was before the scan.
    assert ledger.status("int-live") == CLAIMED
    assert ledger.status("int-ready") == READY
    assert ledger.status("int-unknown") == UNKNOWN
    assert ledger.status("int-done") == ACCEPTED_VERIFIED
    assert ledger.status("int-cancel") == CANCELLED
    assert ledger.status("int-revoked") == UNKNOWN
    assert ledger.status("int-boundary") == CLAIMED
    # The live lease is still claim-blocked, not reclaimed.
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim("int-live", now=now,
                     lease_until=T + timedelta(minutes=5))


def test_repeated_recovery_is_idempotent_in_memory():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    log = LeaseRecoveryLog()

    first = recover_expired_intents(ledger, log, now=T + timedelta(minutes=3),
                                    recovered_by=ACTOR)
    assert first.recovered == ("int1",)
    snapshot = list(log.records)

    # Re-run at a later time: nothing new, nothing reprocessed.
    second = recover_expired_intents(ledger, log,
                                     now=T + timedelta(minutes=4),
                                     recovered_by=ACTOR)
    assert second.scanned == 1
    assert second.recovered == ()
    assert second.already_recovered == ()
    assert list(log.records) == snapshot
    # The reclaimed state stands; history and epoch still untouched.
    assert ledger.status("int1") == READY
    assert len(ledger.get_attempts("int1")) == 1
    assert ledger.ledger_epoch == 0


def test_already_recovered_intent_is_skipped_without_state_change():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    # The log already knows this intent id (e.g. recovered earlier from a
    # different ledger instance or a persisted file).
    log = LeaseRecoveryLog()
    log.append(LeaseRecoveryRecord(
        record_kind=RECORD_KIND, intent_id="int1", route="FEISHU_INFO",
        receipt_id=receipt.receipt_id, attempt_no=1,
        lease_until=T + timedelta(minutes=1),
        recovered_at=T + timedelta(minutes=2),
        reason_code=REASON_LEASE_EXPIRED, recovered_by=ACTOR))

    report = recover_expired_intents(ledger, log, now=T + timedelta(minutes=3),
                                     recovered_by=ACTOR)

    assert report.scanned == 1
    assert report.recovered == ()
    assert report.already_recovered == ("int1",)
    assert len(log.records) == 1
    # No reclaim happened: the intent stays suspended for the operator.
    assert ledger.status("int1") == CLAIMED


def test_jsonl_log_is_append_only_and_byte_idempotent(tmp_path):
    path = str(tmp_path / "lease_recovery.jsonl")
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    now = T + timedelta(minutes=3)

    log = LeaseRecoveryLog(path)
    first = recover_expired_intents(ledger, log, now=now, recovered_by=ACTOR)
    assert first.recovered == ("int1",)
    with open(path, "rb") as handle:
        after_first = handle.read()
    assert after_first.count(b"\n") == 1

    # Repeated scan: file bytes are byte-identical (append-only proof).
    second = recover_expired_intents(ledger, log,
                                     now=T + timedelta(minutes=4),
                                     recovered_by=ACTOR)
    assert second.recovered == ()
    with open(path, "rb") as handle:
        assert handle.read() == after_first

    # A fresh log instance over the same file loads the prior record and
    # still refuses to duplicate it for a re-suspended intent.
    reloaded = LeaseRecoveryLog(path)
    assert reloaded.intent_ids == {"int1"}
    other = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(other, receipt, intent_id="int1")
    third = recover_expired_intents(other, reloaded,
                                    now=T + timedelta(minutes=5),
                                    recovered_by=ACTOR)
    assert third.recovered == ()
    assert third.already_recovered == ("int1",)
    with open(path, "rb") as handle:
        assert handle.read() == after_first
    assert other.status("int1") == CLAIMED

    # A genuinely new suspension appends after the old bytes, never over
    # them.
    claim_and_expire(other, receipt, intent_id="int2")
    fourth = recover_expired_intents(other, reloaded,
                                     now=T + timedelta(minutes=6),
                                     recovered_by=ACTOR)
    assert fourth.recovered == ("int2",)
    with open(path, "rb") as handle:
        grown = handle.read()
    assert grown.startswith(after_first)
    assert grown.count(b"\n") == 2
    lines = grown.splitlines()
    assert json.loads(lines[0].decode("utf-8"))["intent_id"] == "int1"
    assert json.loads(lines[1].decode("utf-8"))["intent_id"] == "int2"


def test_record_round_trips_through_canonical_jsonl(tmp_path):
    path = str(tmp_path / "lease_recovery.jsonl")
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    now = T + timedelta(minutes=3)

    log = LeaseRecoveryLog(path)
    recover_expired_intents(ledger, log, now=now, recovered_by=ACTOR)
    with open(path, "rb") as handle:
        line = handle.read().splitlines()[0]

    # The persisted line is exactly the canonical encoding of the payload.
    assert line == canonical_bytes(log.records[0].payload())
    parsed = json.loads(line.decode("utf-8"))
    assert parsed["lease_until"] == utc_text(T + timedelta(minutes=1))
    assert parsed["recovered_at"] == utc_text(now)
    # A fresh log reconstructs an equal record from the file alone.
    assert LeaseRecoveryLog(path).records == (log.records[0],)


def test_recovered_intent_is_claimable_again_with_budget_preserved():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    log = LeaseRecoveryLog()
    recover_expired_intents(ledger, log, now=T + timedelta(minutes=3),
                            recovered_by=ACTOR)
    # Recovery restores the sanctioned claim path; attempt budget is kept.
    second = ledger.claim("int1", now=T + timedelta(minutes=4),
                          lease_until=T + timedelta(minutes=9))
    assert second.attempt_no == 2
    assert second.outcome == CLAIMED
    # Exhaust the remaining budget: the third attempt expires and recovery
    # must not reset the cap.
    ledger.finish(second, DEFINITELY_REJECTED, now=T + timedelta(minutes=5),
                  external_message_id="rej-2",
                  observed_at=T + timedelta(minutes=5))
    third = ledger.claim("int1", now=T + timedelta(minutes=6),
                         lease_until=T + timedelta(minutes=7))
    assert third.attempt_no == 3
    # The re-suspended intent is skipped by id: already recovered, never
    # reprocessed, no second record.
    again = recover_expired_intents(ledger, log, now=T + timedelta(minutes=8),
                                    recovered_by=ACTOR)
    assert again.recovered == ()
    assert again.already_recovered == ("int1",)
    assert len(log.records) == 1
    assert ledger.status("int1") == CLAIMED
    # The operator escape hatch (W-Q1 explicit reclaim) still enforces the
    # exhausted attempt budget, proving recovery never reset it.
    ledger.reclaim_expired("int1", now=T + timedelta(minutes=9))
    with pytest.raises(DispatchLedgerError, match="ATTEMPTS_EXHAUSTED"):
        ledger.claim("int1", now=T + timedelta(minutes=9),
                     lease_until=T + timedelta(minutes=10))


def test_multiple_suspended_intents_recovered_in_deterministic_order():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int-b",
                     target_ref="feishu:chanB")
    claim_and_expire(ledger, receipt, intent_id="int-a",
                     target_ref="feishu:chanA")
    # One live lease among the suspended ones.
    plan(ledger, receipt, intent_id="int-live")
    ledger.claim("int-live", now=T, lease_until=T + timedelta(minutes=5))

    log = LeaseRecoveryLog()
    report = recover_expired_intents(ledger, log, now=T + timedelta(minutes=3),
                                     recovered_by=ACTOR)

    assert report.scanned == 3
    # list_intents order: (created_at, intent_id) - both created at T.
    assert report.recovered == ("int-a", "int-b")
    assert [record.intent_id for record in log.records] == ["int-a", "int-b"]
    assert ledger.status("int-live") == CLAIMED


def test_memory_log_never_touches_the_filesystem(tmp_path, monkeypatch):
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")

    def fail_open(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("memory log must not open files")

    monkeypatch.setattr("builtins.open", fail_open)
    log = LeaseRecoveryLog()
    report = recover_expired_intents(ledger, log, now=T + timedelta(minutes=3),
                                     recovered_by=ACTOR)
    assert report.recovered == ("int1",)
    assert log.path is None
    assert len(log.records) == 1


def test_missing_log_file_loads_as_empty(tmp_path):
    path = str(tmp_path / "absent.jsonl")
    log = LeaseRecoveryLog(path)
    assert log.records == ()
    assert log.intent_ids == frozenset()
    assert log.has_intent("int1") is False


# ---------------------------------------------------------------------------
# fail-closed validation
# ---------------------------------------------------------------------------

def test_recover_rejects_naive_and_nonzero_offset_now():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    log = LeaseRecoveryLog()
    naive = datetime(2026, 10, 19, 3, 0)
    offset = datetime(2026, 10, 19, 11, 0,
                      tzinfo=timezone(timedelta(hours=8)))
    for bad in (naive, offset):
        with pytest.raises(LeaseRecoveryError, match="RECOVER_NOW_INVALID"):
            recover_expired_intents(ledger, log, now=bad, recovered_by=ACTOR)
    assert ledger.status("int1") == CLAIMED
    assert log.records == ()


def test_recover_rejects_invalid_actor_and_argument_types():
    inbox, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    claim_and_expire(ledger, receipt, intent_id="int1")
    log = LeaseRecoveryLog()
    now = T + timedelta(minutes=3)
    for bad in ("", "bad actor", "bad/actor", 7, None):
        with pytest.raises(LeaseRecoveryError, match="RECOVERED_BY_INVALID"):
            recover_expired_intents(ledger, log, now=now, recovered_by=bad)
    with pytest.raises(LeaseRecoveryError, match="LEDGER_INVALID"):
        recover_expired_intents(object(), log, now=now, recovered_by=ACTOR)
    with pytest.raises(LeaseRecoveryError, match="LOG_INVALID"):
        recover_expired_intents(ledger, object(), now=now, recovered_by=ACTOR)
    assert ledger.status("int1") == CLAIMED
    assert log.records == ()


def test_log_append_fails_closed_on_duplicate_intent_and_type():
    inbox, receipt = fixture_inbox()
    log = LeaseRecoveryLog()
    record = LeaseRecoveryRecord(
        record_kind=RECORD_KIND, intent_id="int1", route="FEISHU_INFO",
        receipt_id=receipt.receipt_id, attempt_no=1,
        lease_until=T + timedelta(minutes=1),
        recovered_at=T + timedelta(minutes=2),
        reason_code=REASON_LEASE_EXPIRED, recovered_by=ACTOR)
    assert log.append(record) is record
    with pytest.raises(LeaseRecoveryError, match="RECORD_INTENT_DUPLICATE"):
        log.append(record)
    with pytest.raises(LeaseRecoveryError, match="RECORD_INVALID"):
        log.append(object())
    assert len(log.records) == 1


def test_record_rejects_broken_time_order_and_fields():
    inbox, receipt = fixture_inbox()
    base = dict(record_kind=RECORD_KIND, intent_id="int1",
                route="FEISHU_INFO", receipt_id=receipt.receipt_id,
                attempt_no=1, lease_until=T + timedelta(minutes=1),
                recovered_at=T + timedelta(minutes=2),
                reason_code=REASON_LEASE_EXPIRED, recovered_by=ACTOR)
    # Recovery time must be strictly after lease expiry.
    with pytest.raises(LeaseRecoveryError, match="RECORD_TIME_ORDER_INVALID"):
        LeaseRecoveryRecord(**dict(base, recovered_at=T + timedelta(minutes=1)))
    with pytest.raises(LeaseRecoveryError, match="RECORD_TIME_ORDER_INVALID"):
        LeaseRecoveryRecord(**dict(base, recovered_at=T))
    for field, bad in (("intent_id", "bad id"), ("route", "SIM_DISPATCH"),
                       ("attempt_no", 0), ("reason_code", "OTHER"),
                       ("record_kind", "OTHER"), ("recovered_by", "")):
        with pytest.raises(LeaseRecoveryError):
            LeaseRecoveryRecord(**dict(base, **{field: bad}))


def test_log_load_rejects_non_canonical_or_malformed_lines(tmp_path):
    good = LeaseRecoveryLog()
    good.append(LeaseRecoveryRecord(
        record_kind=RECORD_KIND, intent_id="int1", route="FEISHU_INFO",
        receipt_id="rcpt1", attempt_no=1,
        lease_until=T + timedelta(minutes=1),
        recovered_at=T + timedelta(minutes=2),
        reason_code=REASON_LEASE_EXPIRED, recovered_by=ACTOR))
    canonical = canonical_bytes(good.records[0].payload()) + b"\n"

    cases = {
        "not json": b"{oops\n",
        "duplicate key": (b'{"attempt_no":1,"attempt_no":1,"intent_id":"int1",'
                          b'"lease_until":"2026-10-19T02:01:00.000000Z",'
                          b'"reason_code":"LEASE_EXPIRED_RECLAIM",'
                          b'"receipt_id":"rcpt1",'
                          b'"recovered_at":"2026-10-19T02:02:00.000000Z",'
                          b'"recovered_by":"quant-ops-1",'
                          b'"record_kind":"LEASE_RECOVERY",'
                          b'"route":"FEISHU_INFO"}\n'),
        "missing field": (b'{"intent_id":"int1","route":"FEISHU_INFO",'
                          b'"receipt_id":"rcpt1","attempt_no":1,'
                          b'"lease_until":"2026-10-19T02:01:00.000000Z",'
                          b'"recovered_at":"2026-10-19T02:02:00.000000Z",'
                          b'"reason_code":"LEASE_EXPIRED_RECLAIM",'
                          b'"recovered_by":"quant-ops-1"}\n'),
        "non canonical": b'{"attempt_no": 1}\n',
    }
    for name, payload in cases.items():
        path = tmp_path / ("bad-%s.jsonl" % name.replace(" ", "-"))
        path.write_bytes(payload)
        with pytest.raises(LeaseRecoveryError):
            LeaseRecoveryLog(str(path))
    # The canonical line itself loads cleanly.
    ok = tmp_path / "ok.jsonl"
    ok.write_bytes(canonical)
    assert LeaseRecoveryLog(str(ok)).records == good.records


def test_log_rejects_invalid_path_type():
    with pytest.raises(LeaseRecoveryError, match="LOG_PATH_INVALID"):
        LeaseRecoveryLog("")
    with pytest.raises(LeaseRecoveryError, match="LOG_PATH_INVALID"):
        LeaseRecoveryLog(7)
