"""Offline adversarial tests for the per-target delivery journal."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_backtest.delivery_ledger import (  # noqa: E402
    Claim, DeliveryLedger, NonAcceptanceProof, ProviderReceipt, event_key, target_key,
)


class FakeClock:
    """Deterministic UTC time source."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **parts: int) -> None:
        self.value += timedelta(**parts)


def provider_receipt(claim: Claim, observed_at: datetime) -> ProviderReceipt:
    """Create a synthetic independent-provider evidence envelope."""
    return ProviderReceipt(claim.event_key, claim.target_key, claim.attempt_id,
                           claim.route, "provider-message-001", claim.trace_id,
                           observed_at)


def non_acceptance_proof(claim: Claim, observed_at: datetime) -> NonAcceptanceProof:
    """Create a test-only synthetic provider definitive-rejection envelope."""
    return NonAcceptanceProof(claim.event_key, claim.target_key, claim.attempt_id,
                              claim.route, claim.trace_id, "provider_rejected",
                              "provider-rejected", observed_at)


class DeliveryLedgerTests(unittest.TestCase):
    """Tests use only temporary local SQLite and fake sender receipts."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.clock = FakeClock()
        self.ledger = DeliveryLedger(
            self.path, clock=self.clock,
            receipt_verifier=lambda receipt: receipt.receipt_id == "provider-message-001",
            non_acceptance_verifier=lambda proof: proof.reason == "provider-rejected")
        self.event, self.target = self.ledger.register(
            "subscription-17", "quote-alert", "live", "sha256:content-a",
            "quote:2026-10-18T10:00:00Z", "feishu_open_id", "ou_123")

    def test_identity_is_stable_across_day_and_alias_and_changes_by_quote(self) -> None:
        self.clock.advance(days=1)
        self.assertEqual((self.event, self.target), self.ledger.register(
            "subscription-17", "quote-alert", "live", "sha256:content-a",
            "quote:2026-10-18T10:00:00Z", "feishu_open_id", "ou_123"))
        self.assertNotEqual(self.event, event_key("subscription-17", "quote-alert", "live",
                                                   "sha256:content-a", "quote:2026-10-19T10:00:00Z"))
        self.assertNotEqual(self.event, event_key("subscription-17", "quote-alert", "live",
                                                   "sha256:content-b", "quote:2026-10-18T10:00:00Z"))
        self.assertEqual(self.target, target_key("feishu_open_id", "ou_123"))
        self.assertNotEqual(self.target, target_key("feishu_chat_id", "ou_123"))
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(1, db.execute("SELECT count(*) FROM delivery_targets").fetchone()[0])
            self.assertEqual(1, db.execute("PRAGMA user_version").fetchone()[0])
        finally:
            db.close()
        with self.assertRaises(ValueError):
            event_key("subscription-17", "quote-alert", "live", "", "quote:time")

    def test_partial_success_and_definitive_failure_retry_only_failed_target(self) -> None:
        _, target_b = self.ledger.register("subscription-17", "quote-alert", "live",
                                            "sha256:content-a", "quote:2026-10-18T10:00:00Z",
                                            "feishu_open_id", "ou_456")
        claim_a = self.ledger.claim(self.event, self.target, "feishu/open_id")
        claim_b = self.ledger.claim(self.event, target_b, "feishu/open_id")
        self.assertIsNotNone(claim_a)
        self.assertIsNotNone(claim_b)
        self.assertTrue(self.ledger.finish(claim_a, outcome="SENT",
                                           receipt=provider_receipt(claim_a, self.clock())))
        self.assertTrue(self.ledger.finish(claim_b, outcome="FAILED",
                                           non_acceptance=non_acceptance_proof(claim_b, self.clock())))
        self.assertIsNone(self.ledger.claim(self.event, self.target, "feishu/open_id", retry_failed=True))
        self.assertIsNone(self.ledger.claim(self.event, target_b, "feishu/open_id"))
        retry = self.ledger.claim(self.event, target_b, "feishu/open_id", retry_failed=True)
        self.assertIsNotNone(retry)
        self.assertNotEqual(claim_b.attempt_id, retry.attempt_id)
        self.assertEqual(["FAILED", "CLAIMED"],
                         [row["outcome"] for row in self.ledger.attempts(self.event, target_b)])
        self.assertEqual("provider-message-001", self.ledger.get(self.event, self.target)["receipt_id"])

    def test_no_receipt_fake_receipt_and_bool_success_never_mark_sent(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "feishu/open_id")
        self.assertTrue(self.ledger.finish(claim, outcome="SENT", receipt=True))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertIsNone(self.ledger.get(self.event, self.target)["receipt_id"])
        self.assertIsNone(self.ledger.claim(self.event, self.target, "feishu/open_id", retry_failed=True))
        other = DeliveryLedger(Path(self.temp.name) / "no-verifier.sqlite3", clock=self.clock)
        event, target = other.register("s", "k", "m", "fingerprint", "source:time", "id", "a")
        attempt = other.claim(event, target, "r")
        self.assertTrue(other.finish(attempt, outcome="SENT", receipt=provider_receipt(attempt, self.clock())))
        self.assertEqual("UNKNOWN", other.get(event, target)["state"])
        self.assertIsNone(other.get(event, target)["receipt_id"])
        fake = self.ledger.register("s", "k", "m", "fingerprint", "source:time", "id", "b")
        fake_claim = self.ledger.claim(*fake, "r")
        wrong = ProviderReceipt(fake_claim.event_key, fake_claim.target_key, "different", "r",
                                "provider-id", fake_claim.trace_id, self.clock())
        self.assertTrue(self.ledger.finish(fake_claim, outcome="SENT", receipt=wrong))
        self.assertEqual("UNKNOWN", self.ledger.get(*fake)["state"])

    def test_timeout_expiry_and_restart_are_blocked_not_retried(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "r", lease=timedelta(seconds=10))
        self.clock.advance(seconds=11)
        restarted = DeliveryLedger(self.path, clock=self.clock, receipt_verifier=lambda receipt: True)
        self.assertEqual(1, restarted.expire())
        self.assertEqual(0, restarted.expire())
        self.assertEqual("UNKNOWN", restarted.get(self.event, self.target)["state"])
        self.assertEqual("UNKNOWN", restarted.attempts(self.event, self.target)[0]["outcome"])
        self.assertIsNone(restarted.claim(self.event, self.target, "r", retry_failed=True))
        self.assertFalse(restarted.finish(claim, outcome="SENT",
                                           receipt=provider_receipt(claim, self.clock())))
        self.assertEqual("UNKNOWN", restarted.get(self.event, self.target)["state"])

    def test_late_completion_without_expiry_call_is_also_unknown(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "r", lease=timedelta(seconds=1))
        self.clock.advance(seconds=2)
        self.assertTrue(self.ledger.finish(claim, outcome="FAILED",
                                           non_acceptance=non_acceptance_proof(claim, self.clock())))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])

    def test_cancel_blocks_future_claim_and_tracks_inflight_uncertainty(self) -> None:
        extra_event, extra_target = self.ledger.register("subscription-17", "quote-alert", "live",
                                                          "sha256:content-a", "quote:2026-10-18T10:00:00Z",
                                                          "feishu_open_id", "ou_extra")
        self.assertEqual(self.event, extra_event)
        claim = self.ledger.claim(self.event, self.target, "r")
        self.assertTrue(self.ledger.cancel(self.event))
        self.assertFalse(self.ledger.cancel(self.event))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertEqual("CANCELLED", self.ledger.get(self.event, extra_target)["state"])
        self.assertIsNone(self.ledger.claim(self.event, extra_target, "r", retry_failed=True))
        self.assertFalse(self.ledger.finish(claim, outcome="SENT", receipt=provider_receipt(claim, self.clock())))
        with self.assertRaises(ValueError):
            self.ledger.register("subscription-17", "quote-alert", "live", "sha256:content-a",
                                 "quote:2026-10-18T10:00:00Z", "feishu_open_id", "ou_new")

    def test_two_threads_have_exactly_one_claim_and_one_attempt(self) -> None:
        barrier = threading.Barrier(2)
        results: list[Claim | None] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait(timeout=5)
            result = DeliveryLedger(self.path, clock=self.clock).claim(self.event, self.target, "r")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, sum(claim is not None for claim in results))
        self.assertEqual(1, len(self.ledger.attempts(self.event, self.target)))

    def test_failed_without_verified_non_acceptance_becomes_unknown(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "r")
        self.assertTrue(self.ledger.finish(claim, outcome="FAILED"))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        self.assertIsNone(self.ledger.claim(self.event, self.target, "r", retry_failed=True))

    def test_unrecognized_failure_type_cannot_enable_retry_even_with_naive_verifier(self) -> None:
        naive = DeliveryLedger(self.path, clock=self.clock,
                               non_acceptance_verifier=lambda proof: True)
        claim = naive.claim(self.event, self.target, "r")
        forged = replace(non_acceptance_proof(claim, self.clock()),
                         evidence_type="timeout")
        self.assertTrue(naive.finish(claim, outcome="FAILED", non_acceptance=forged))
        self.assertEqual("UNKNOWN", naive.get(self.event, self.target)["state"])
        self.assertIsNone(naive.claim(self.event, self.target, "r", retry_failed=True))

    def test_true_verifier_is_trust_injection_not_proof_of_provider_authenticity(self) -> None:
        unsafe = DeliveryLedger(self.path, clock=self.clock,
                                receipt_verifier=lambda receipt: True)
        claim = unsafe.claim(self.event, self.target, "r")
        fabricated = replace(provider_receipt(claim, self.clock()),
                             receipt_id="fabricated-local-message-id")
        self.assertTrue(unsafe.finish(claim, outcome="SENT", receipt=fabricated))
        self.assertEqual("SENT", unsafe.get(self.event, self.target)["state"])
        # A lambda-True verifier has explicitly assumed the trust boundary; this
        # test proves the ledger cannot authenticate provider evidence by itself.
        self.assertEqual("fabricated-local-message-id",
                         unsafe.get(self.event, self.target)["receipt_id"])

    def test_forged_trace_and_verifier_exception_fail_closed(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "r")
        self.assertEqual(claim.trace_id, self.ledger.attempts(self.event, self.target)[0]["trace_id"])
        forged = Claim(claim.event_key, claim.target_key, claim.attempt_id,
                       claim.route, claim.expires_at, "not-the-durable-trace")
        self.assertFalse(self.ledger.finish(forged, outcome="SENT",
                                            receipt=provider_receipt(forged, self.clock())))
        forged_receipt = ProviderReceipt(claim.event_key, claim.target_key, claim.attempt_id,
                                         claim.route, "provider-message-001", "wrong-trace", self.clock())
        self.assertTrue(self.ledger.finish(claim, outcome="SENT", receipt=forged_receipt))
        self.assertEqual("UNKNOWN", self.ledger.get(self.event, self.target)["state"])
        other = DeliveryLedger(Path(self.temp.name) / "throwing.sqlite3", clock=self.clock,
                               receipt_verifier=lambda receipt: 1 / 0)
        event, target = other.register("s", "k", "m", "f", "a", "type", "id")
        attempt = other.claim(event, target, "r")
        self.assertTrue(other.finish(attempt, outcome="SENT",
                                     receipt=provider_receipt(attempt, self.clock())))
        self.assertEqual("UNKNOWN", other.get(event, target)["state"])

    def test_wrong_attempt_and_invalid_clock_are_rejected(self) -> None:
        claim = self.ledger.claim(self.event, self.target, "r")
        wrong = Claim(claim.event_key, claim.target_key, "wrong", claim.route,
                      claim.expires_at, claim.trace_id)
        self.assertFalse(self.ledger.finish(wrong, outcome="FAILED"))
        self.assertEqual("CLAIMED", self.ledger.get(self.event, self.target)["state"])
        with self.assertRaises(ValueError):
            DeliveryLedger(Path(self.temp.name) / "bad-clock.sqlite3",
                           clock=lambda: datetime(2026, 10, 18)).register(
                               "s", "k", "m", "f", "a", "type", "id")


if __name__ == "__main__":
    unittest.main()
