"""Isolated SQLite adversarial tests for per-target delivery admission."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_backtest.delivery_ledger import (  # noqa: E402
    Claim, DeliveryLedger, NonAcceptanceProof, ProviderReceipt, event_key, target_key,
)


class FakeClock:
    """Deterministic, timezone-aware test clock."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **parts: int) -> None:
        self.value += timedelta(**parts)


def provider_receipt(claim: Claim, observed_at: datetime) -> ProviderReceipt:
    """Construct synthetic provider evidence for a fake verifier."""
    return ProviderReceipt(claim.event_key, claim.target_key, claim.attempt_id,
                           claim.route, "provider-message-001", claim.trace_id, observed_at)


def non_acceptance_proof(claim: Claim, observed_at: datetime) -> NonAcceptanceProof:
    """Construct synthetic definitive rejection evidence."""
    return NonAcceptanceProof(claim.event_key, claim.target_key, claim.attempt_id,
                              claim.route, claim.trace_id, "provider_rejected",
                              "provider-rejected", observed_at)


class DeliveryLedgerTests(unittest.TestCase):
    """All fixtures stay in temporary SQLite and have no real provider I/O."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.clock = FakeClock()
        self.ledger = DeliveryLedger(
            self.path, clock=self.clock,
            receipt_verifier=lambda receipt: receipt.receipt_id == "provider-message-001",
            non_acceptance_verifier=lambda proof: proof.reason == "provider-rejected")
        self.ledger.set_subscription_control("subscription-17", enabled=True, epoch=1)
        self.event, self.target = self.register()

    def register(self, number: int = 0, target_id: str = "ou_123", *,
                 sub_id: str = "subscription-17", source_at: datetime | None = None,
                 valid_until: datetime | None = None, payload: str | None = None,
                 epoch: int = 1) -> tuple[str, str]:
        """Register one new immutable quote under explicit control."""
        source = source_at or self.clock()
        until = valid_until or self.clock() + timedelta(days=3)
        return self.ledger.register(
            sub_id, "quote-alert", "live", f"sha256:content-{number}",
            f"quote:{number}", "feishu_open_id", target_id,
            source_at=source, payload=payload or f"frozen-payload-{number}",
            valid_until=until, expected_epoch=epoch)

    def claim(self, event: str | None = None, target: str | None = None,
              **kwargs: object) -> Claim | None:
        """Claim with test's expected authorization epoch."""
        return self.ledger.claim(event or self.event, target or self.target,
                                 "feishu/open_id", expected_epoch=1, **kwargs)

    def test_event_identity_and_frozen_payload(self) -> None:
        self.assertEqual((self.event, self.target), self.register())
        self.assertEqual(self.target, target_key("feishu_open_id", "ou_123"))
        self.assertNotEqual(self.event, event_key("subscription-17", "quote-alert", "live",
                                                   "sha256:content-2", "quote:2"))
        with self.assertRaisesRegex(ValueError, "identity conflict"):
            self.register(payload="different bytes")
        db = sqlite3.connect(self.path)
        try:
            stored = db.execute("SELECT payload,payload_hash,source_day_cst FROM delivery_events").fetchone()
            self.assertEqual("frozen-payload-0", stored[0])
            self.assertEqual(64, len(stored[1]))
            self.assertEqual("2026-10-18", stored[2])
            self.assertEqual(2, db.execute("PRAGMA user_version").fetchone()[0])
        finally:
            db.close()

    def test_legacy_registration_and_claim_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            self.ledger.register("s", "k", "m", "f", "a", "type", "id")
        with self.assertRaisesRegex(ValueError, "expected_epoch"):
            self.ledger.claim(self.event, self.target, "r")
        other = DeliveryLedger(Path(self.temp.name) / "uncontrolled.sqlite3", clock=self.clock)
        with self.assertRaisesRegex(ValueError, "authorization"):
            other.register("s", "k", "m", "f", "a", "type", "id",
                           source_at=self.clock(), payload="p",
                           valid_until=self.clock() + timedelta(hours=1), expected_epoch=1)

    def test_partial_success_failed_target_retries_only_after_backoff(self) -> None:
        _, target_b = self.register(target_id="ou_456")
        claim_a = self.claim()
        claim_b = self.claim(target=target_b)
        self.assertIsNotNone(claim_a)
        self.assertIsNotNone(claim_b)
        self.assertTrue(self.ledger.finish(claim_a, outcome="SENT",
                                           receipt=provider_receipt(claim_a, self.clock())))
        self.assertTrue(self.ledger.finish(claim_b, outcome="FAILED",
                                           non_acceptance=non_acceptance_proof(claim_b, self.clock())))
        self.assertIsNone(self.claim(retry_failed=True))
        self.assertIsNone(self.claim(target=target_b, retry_failed=True))
        self.clock.advance(minutes=1)
        retry = self.claim(target=target_b, retry_failed=True)
        self.assertIsNotNone(retry)
        self.assertNotEqual(claim_b.attempt_id, retry.attempt_id)
        self.assertEqual(["FAILED", "CLAIMED"],
                         [row["outcome"] for row in self.ledger.attempts(self.event, target_b)])
        self.assertEqual("provider-message-001", self.ledger.get(self.event, self.target)["receipt_id"])

    def test_daily_cap_reserves_distinct_events_not_targets(self) -> None:
        a = self.claim()
        self.assertIsNotNone(a)
        _, second_target = self.register(target_id="ou_other")
        self.assertIsNotNone(self.claim(target=second_target))
        b = self.register(1)
        c = self.register(2)
        d = self.register(3)
        b_claim = self.claim(*b)
        self.assertIsNotNone(b_claim)
        self.assertIsNotNone(self.claim(*c))
        self.assertIsNone(self.claim(*d))
        self.assertTrue(self.ledger.finish(b_claim, outcome="FAILED",
                                           non_acceptance=non_acceptance_proof(b_claim, self.clock())))
        self.assertIsNotNone(self.claim(*d))

    def test_concurrent_claim_on_last_slot_has_single_winner(self) -> None:
        self.assertIsNotNone(self.claim())
        self.assertIsNotNone(self.claim(*self.register(1)))
        candidates = [self.register(2), self.register(3)]
        barrier = threading.Barrier(2)
        results: list[Claim | None] = []
        lock = threading.Lock()

        def worker(pair: tuple[str, str]) -> None:
            barrier.wait(timeout=5)
            result = DeliveryLedger(self.path, clock=self.clock).claim(
                *pair, "r", expected_epoch=1)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker, args=(pair,)) for pair in candidates]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, sum(result is not None for result in results))

    def test_cst_source_day_not_send_day_and_stale_replay_does_not_rebill(self) -> None:
        self.clock.advance(hours=7)
        source_day_one = datetime(2026, 10, 18, 15, 59, tzinfo=timezone.utc)
        source_day_two = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)
        stable_until = self.clock() + timedelta(days=3)
        before = self.register(1, source_at=source_day_one, valid_until=stable_until)
        after = self.register(2, source_at=source_day_two, valid_until=stable_until)
        self.assertIsNotNone(self.claim(*before))
        self.assertIsNotNone(self.claim(*after))
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(["2026-10-18", "2026-10-19"],
                             [row[0] for row in db.execute(
                                 "SELECT source_day_cst FROM delivery_events WHERE event_key IN (?,?) "
                                 "ORDER BY source_day_cst", (before[0], after[0]))])
        finally:
            db.close()
        self.clock.advance(days=1)
        self.assertEqual(before, self.register(1, source_at=source_day_one,
                                               valid_until=stable_until))
        self.assertIsNone(self.claim(*before))

    def test_revoke_inflight_unknown_and_reenable_requires_new_epoch(self) -> None:
        ready = self.register(1)
        claim = self.claim()
        self.ledger.set_subscription_control("subscription-17", enabled=False, epoch=2)
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertEqual("CANCELLED", self.ledger.get(*ready)["state"])
        self.assertFalse(self.ledger.finish(claim, outcome="SENT",
                                            receipt=provider_receipt(claim, self.clock())))
        self.assertIsNone(self.claim(*ready))
        self.ledger.set_subscription_control("subscription-17", enabled=True, epoch=3)
        self.assertIsNone(self.claim(*ready))
        with self.assertRaisesRegex(ValueError, "stale"):
            self.ledger.set_subscription_control("subscription-17", enabled=True, epoch=2)
        with self.assertRaisesRegex(ValueError, "identity conflict"):
            self.register(1, epoch=3)
        fresh = self.register(2, epoch=3)
        self.assertIsNotNone(self.ledger.claim(*fresh, "r", expected_epoch=3))

    def test_cancel_event_preserves_inflight_uncertainty(self) -> None:
        ready = self.register(target_id="ou_extra")
        claim = self.claim()
        self.assertEqual(ready[0], self.event)
        self.assertTrue(self.ledger.cancel(self.event))
        self.assertFalse(self.ledger.cancel(self.event))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertEqual("CANCELLED", self.ledger.get(*ready)["state"])
        self.assertFalse(self.ledger.finish(claim, outcome="SENT",
                                            receipt=provider_receipt(claim, self.clock())))
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.register(target_id="ou_new")

    def test_attempt_ceiling_and_exponential_backoff(self) -> None:
        for index in range(3):
            claim = self.claim(retry_failed=index > 0)
            self.assertIsNotNone(claim)
            self.assertTrue(self.ledger.finish(claim, outcome="FAILED",
                                               non_acceptance=non_acceptance_proof(claim, self.clock())))
            self.assertIsNone(self.claim(retry_failed=True))
            self.clock.advance(minutes=2 ** index)
        self.assertIsNone(self.claim(retry_failed=True))
        self.assertEqual(3, len(self.ledger.attempts(self.event, self.target)))

    def test_expiry_blocks_first_claim_or_retry_whose_lease_crosses_deadline(self) -> None:
        short = self.register(1, valid_until=self.clock() + timedelta(minutes=6))
        self.assertIsNotNone(self.claim(*short))
        self.clock.advance(minutes=2)
        self.assertIsNone(self.claim(*self.register(2, valid_until=self.clock() + timedelta(minutes=2))))
        self.clock.advance(minutes=5)
        self.assertIsNone(self.claim(*short))

    def test_unknown_never_retries_even_after_restart(self) -> None:
        claim = self.claim(lease=timedelta(seconds=10))
        self.clock.advance(seconds=11)
        restarted = DeliveryLedger(self.path, clock=self.clock, receipt_verifier=lambda receipt: True)
        self.assertEqual(1, restarted.expire())
        self.assertEqual(0, restarted.expire())
        self.assertEqual("UNKNOWN", restarted.get(self.event, self.target)["state"])
        self.assertIsNone(restarted.claim(self.event, self.target, "r",
                                          expected_epoch=1, retry_failed=True))
        self.assertFalse(restarted.finish(claim, outcome="SENT",
                                          receipt=provider_receipt(claim, self.clock())))

    def test_unverified_failed_and_bool_success_are_unknown(self) -> None:
        claim = self.claim()
        self.assertTrue(self.ledger.finish(claim, outcome="SENT", receipt=True))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertIsNone(self.claim(retry_failed=True))
        another = self.register(1)
        c = self.claim(*another)
        self.assertTrue(self.ledger.finish(c, outcome="FAILED"))
        self.assertEqual("UNKNOWN", self.ledger.get(*another)["state"])

    def test_forged_receipt_and_trace_and_untrusted_failure_type(self) -> None:
        claim = self.claim()
        wrong_claim = replace(claim, trace_id="not-the-persisted-trace")
        self.assertFalse(self.ledger.finish(wrong_claim, outcome="SENT",
                                            receipt=provider_receipt(wrong_claim, self.clock())))
        forged = replace(provider_receipt(claim, self.clock()), trace_id="wrong-trace")
        self.assertTrue(self.ledger.finish(claim, outcome="SENT", receipt=forged))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        other = self.register(1)
        second = self.claim(*other)
        false_negative = replace(non_acceptance_proof(second, self.clock()), evidence_type="timeout")
        self.assertTrue(self.ledger.finish(second, outcome="FAILED", non_acceptance=false_negative))
        self.assertEqual("UNKNOWN", self.ledger.get(*other)["state"])

    def test_verifier_is_explicit_external_trust_boundary(self) -> None:
        unsafe = DeliveryLedger(self.path, clock=self.clock, receipt_verifier=lambda receipt: True)
        claim = unsafe.claim(self.event, self.target, "r", expected_epoch=1)
        fabricated = replace(provider_receipt(claim, self.clock()), receipt_id="fabricated-local-id")
        self.assertTrue(unsafe.finish(claim, outcome="SENT", receipt=fabricated))
        self.assertEqual("fabricated-local-id", unsafe.get(self.event, self.target)["receipt_id"])

    def test_legacy_row_without_control_epoch_does_not_authorize(self) -> None:
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE delivery_events SET control_epoch=NULL WHERE event_key=?", (self.event,))
            db.commit()
        finally:
            db.close()
        self.assertIsNone(self.claim())
        with self.assertRaisesRegex(ValueError, "identity conflict"):
            self.register()


if __name__ == "__main__":
    unittest.main()
