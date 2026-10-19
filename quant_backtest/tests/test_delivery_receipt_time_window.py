"""Offline adversarial evidence-time tests for one claimed delivery attempt."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_backtest.delivery_ledger import (  # noqa: E402
    Claim,
    DeliveryLedger,
    NonAcceptanceProof,
    ProviderReceipt,
    _evidence_in_window,
)


class MutableClock:
    """Provide controlled aware instants without network or production time."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class EvidenceTimeWindowTests(unittest.TestCase):
    """Exercise both independent verifier ports and persisted attempt outcomes."""

    def setUp(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        temporary = tempfile.TemporaryDirectory(dir=tests_dir)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "receipts.sqlite3"
        self.clock = MutableClock()
        self.receipt_checks = 0
        self.rejection_checks = 0
        self.ledger = DeliveryLedger(
            self.path,
            clock=self.clock,
            receipt_verifier=self.verify_receipt,
            non_acceptance_verifier=self.verify_rejection,
        )
        self.ledger.set_subscription_control("sub-time", enabled=True, epoch=1)
        self.event, self.target = self.ledger.register(
            "sub-time", "quote", "live", "content-1", "source-1", "open_id", "one",
            source_at=self.clock(), payload="frozen", expected_epoch=1,
            valid_until=self.clock() + timedelta(days=1),
        )

    def verify_receipt(self, receipt: ProviderReceipt) -> bool:
        """Count calls to the synthetic verifier, not authenticate a provider."""
        self.receipt_checks += 1
        return receipt.receipt_id == "synthetic-id"

    def verify_rejection(self, proof: NonAcceptanceProof) -> bool:
        """Count calls to the synthetic non-acceptance verifier."""
        self.rejection_checks += 1
        return proof.reason == "synthetic-rejection"

    def claim_target(self, target_name: str) -> Claim:
        """Start an independent attempt for one recipient of the same event."""
        event, target = self.ledger.register(
            "sub-time", "quote", "live", "content-1", "source-1", "open_id",
            target_name, source_at=self.clock().replace(hour=10, minute=0, second=0),
            payload="frozen", expected_epoch=1,
            valid_until=self.clock().replace(hour=10, minute=0, second=0) + timedelta(days=1),
        )
        claim = self.ledger.claim(event, target, "offline-route", expected_epoch=1,
                                  lease=timedelta(minutes=5))
        self.assertIsNotNone(claim)
        assert claim is not None
        return claim

    @staticmethod
    def evidence(claim: Claim, kind: str, observed_at: datetime) -> ProviderReceipt | NonAcceptanceProof:
        """Make identity-bound synthetic evidence for either outcome."""
        if kind == "SENT":
            return ProviderReceipt(claim.event_key, claim.target_key, claim.attempt_id,
                                   claim.route, "synthetic-id", claim.trace_id, observed_at)
        return NonAcceptanceProof(claim.event_key, claim.target_key, claim.attempt_id,
                                  claim.route, claim.trace_id, "provider_rejected",
                                  "synthetic-rejection", observed_at)

    def finish_with(self, claim: Claim, kind: str, observed_at: datetime) -> bool:
        """Pass evidence through finish, rather than changing ledger internals."""
        evidence = self.evidence(claim, kind, observed_at)
        if kind == "SENT":
            return self.ledger.finish(claim, outcome=kind, receipt=evidence)
        return self.ledger.finish(claim, outcome=kind, non_acceptance=evidence)

    def test_both_proofs_fail_closed_outside_claim_window(self) -> None:
        """An accepting verifier cannot override a preclaim/future/naive instant."""
        scenarios: dict[str, Callable[[datetime], datetime]] = {
            "before_claim": lambda start: start - timedelta(microseconds=1),
            "future_finish": lambda start: start + timedelta(seconds=11),
            "after_expiry": lambda start: start + timedelta(minutes=5, microseconds=1),
            "missing_timezone": lambda start: start.replace(tzinfo=None),
        }
        for kind in ("SENT", "FAILED"):
            for name, observation in scenarios.items():
                with self.subTest(kind=kind, scenario=name):
                    self.clock.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
                    claim = self.claim_target(f"{kind}-{name}")
                    claimed_at = datetime.fromisoformat(
                        str(self.ledger.get(claim.event_key, claim.target_key)["claimed_at"]))
                    self.clock.value = claimed_at + timedelta(seconds=10)
                    before = self.receipt_checks + self.rejection_checks
                    self.assertTrue(self.finish_with(claim, kind, observation(claimed_at)))
                    self.assertEqual(before, self.receipt_checks + self.rejection_checks)
                    row = self.ledger.get(claim.event_key, claim.target_key)
                    self.assertIsNotNone(row)
                    self.assertEqual("UNKNOWN", row["state"])
                    self.assertIsNone(row["retry_after"])
                    self.assertIsNone(row["receipt_id"])
                    self.assertEqual("UNKNOWN", self.ledger.attempts(
                        claim.event_key, claim.target_key)[0]["outcome"])
                    self.clock.value += timedelta(minutes=10)
                    reopened = DeliveryLedger(self.path, clock=self.clock,
                                              receipt_verifier=lambda receipt: True,
                                              non_acceptance_verifier=lambda proof: True)
                    self.assertEqual("UNKNOWN", reopened.get(
                        claim.event_key, claim.target_key)["state"])
                    self.assertIsNone(reopened.claim(claim.event_key, claim.target_key,
                                                     "offline-route", expected_epoch=1,
                                                     retry_failed=True))
                    self.assertFalse(reopened.finish(claim, outcome=kind))

    def test_both_proofs_accept_inclusive_claim_and_finish_boundaries(self) -> None:
        """UTC-equivalent offsets and microsecond edge instants are inclusive."""
        for kind in ("SENT", "FAILED"):
            for boundary in ("claimed_at", "finish_now"):
                with self.subTest(kind=kind, boundary=boundary):
                    self.clock.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
                    claim = self.claim_target(f"{kind}-{boundary}")
                    claimed = self.clock()
                    self.clock.value += timedelta(seconds=10, microseconds=123)
                    observed = claimed if boundary == "claimed_at" else self.clock()
                    observed_in_cst = observed.astimezone(timezone(timedelta(hours=8)))
                    before = self.receipt_checks + self.rejection_checks
                    self.assertTrue(self.finish_with(claim, kind, observed_in_cst))
                    self.assertEqual(before + 1, self.receipt_checks + self.rejection_checks)
                    self.assertEqual(kind, self.ledger.get(claim.event_key, claim.target_key)["state"])
                    self.assertEqual(kind, self.ledger.attempts(
                        claim.event_key, claim.target_key)[0]["outcome"])
                    reopened = DeliveryLedger(self.path, clock=self.clock)
                    self.assertEqual(kind, reopened.get(claim.event_key, claim.target_key)["state"])
                    if kind == "SENT":
                        self.assertEqual("synthetic-id", reopened.get(
                            claim.event_key, claim.target_key)["receipt_id"])
                    else:
                        self.assertIsNotNone(reopened.get(
                            claim.event_key, claim.target_key)["retry_after"])

    def test_expiry_endpoint_inclusive_only_for_evidence_not_expired_finish(self) -> None:
        """A proof exactly at lease expiry is within the interval, but cannot finish late."""
        claim = self.claim_target("expiry-end")
        start = str(self.ledger.get(claim.event_key, claim.target_key)["claimed_at"])
        expires = claim.expires_at
        at_expiry = datetime.fromisoformat(expires)
        self.assertTrue(_evidence_in_window(at_expiry, start, expires, expires))
        self.assertFalse(_evidence_in_window(
            at_expiry + timedelta(microseconds=1), start, expires, expires))
        self.clock.value = at_expiry
        self.assertTrue(self.finish_with(claim, "SENT", at_expiry))
        self.assertEqual("UNKNOWN", self.ledger.get(claim.event_key, claim.target_key)["state"])
        self.assertEqual(0, self.receipt_checks)

    def test_verifier_false_or_exception_remains_unknown_for_valid_time(self) -> None:
        """The timestamp gate is additional to, not a replacement for, verification."""
        for kind in ("SENT", "FAILED"):
            for behavior in ("false", "raises"):
                with self.subTest(kind=kind, behavior=behavior):
                    self.clock.value = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
                    claim = self.claim_target(f"{kind}-{behavior}")
                    proof = self.evidence(claim, kind, self.clock())
                    if kind == "SENT":
                        proof = replace(proof, receipt_id="not-synthetic-id")
                    else:
                        proof = replace(proof, reason="not-synthetic-rejection")
                    if behavior == "raises":
                        def reject(_evidence: object) -> bool:
                            raise RuntimeError("offline verifier unavailable")
                        if kind == "SENT":
                            self.ledger.receipt_verifier = reject
                        else:
                            self.ledger.non_acceptance_verifier = reject
                    if kind == "SENT":
                        self.assertTrue(self.ledger.finish(claim, outcome=kind, receipt=proof))
                    else:
                        self.assertTrue(self.ledger.finish(claim, outcome=kind,
                                                           non_acceptance=proof))
                    self.assertEqual("UNKNOWN", self.ledger.get(
                        claim.event_key, claim.target_key)["state"])
                    self.ledger.receipt_verifier = self.verify_receipt
                    self.ledger.non_acceptance_verifier = self.verify_rejection


if __name__ == "__main__":
    unittest.main()
