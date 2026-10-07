"""验证人工审核历史的落库时点、一致快照和持续来源缺口。"""

from contextlib import ExitStack
import copy
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
import uuid

from ai_operator.contracts import EVENT_FIELDS, canonical_json, normalize_time, utc_now
from ai_operator.memory import open_store
from tests_ai_operator import test_review_gate as gate_fixtures


class SignedFixtureVerifier:
    """仅供测试的声明签名适配器，正式源码不能默认接纳此凭证。"""

    key = b"review-history-engineering-fixture-only"
    principal = {"principal_id": "fixture-reviewer", "authority_id": "fixture-authority",
                 "policy_id": "fixture-policy", "role": "source_reviewer"}

    def proof(self, statement):
        return {"signature": hmac.new(self.key, canonical_json(statement).encode("utf-8"), hashlib.sha256).hexdigest()}

    def verify(self, statement, proof, *, received_at):
        expected = self.proof(statement)
        if type(proof) is not dict or set(proof) != {"signature"} or not hmac.compare_digest(proof["signature"], expected["signature"]):
            raise ValueError("工程签名与声明不匹配")
        return dict(self.principal)


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gate_fixtures.ReviewGateTests("test_reopen_and_gate_missing_evidence")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.receipt = self.fixture.capture()
        self.review = self.fixture.review(self.receipt, self.fixture.matched_evidence(self.receipt))
        self.base = datetime.fromisoformat(self.review["recorded_available_at"]) + timedelta(seconds=1)
        self.verifier = SignedFixtureVerifier()

    def instant(self, second):
        return (self.base + timedelta(seconds=second)).isoformat(timespec="microseconds")

    def clock(self, second):
        stack = ExitStack()
        value = self.instant(second)
        stack.enter_context(patch("ai_operator.contracts.utc_now", return_value=value))
        stack.enter_context(patch("ai_operator.memory.utc_now", return_value=value))
        stack.enter_context(patch("ai_operator.review_gate.utc_now", return_value=value))
        # 两个新模块可复用contracts，也可按现有模式导入utc_now。
        for module in ("human_review", "review_history"):
            try:
                stack.enter_context(patch("ai_operator." + module + ".utc_now", return_value=value, create=True))
            except ModuleNotFoundError:
                pass
        return stack

    def record(self, action, second, *, target=None, expires=100, pair=None):
        from ai_operator.human_review import record_human_decision
        capture, review = pair or (self.receipt, self.review)
        statement = {"schema_version": 1, "request_id": uuid.uuid4().hex, "action": action,
                     "capture_event_id": capture["capture_event_id"], "review_event_id": review["review_event_id"],
                     "target_decision_event_id": target, "reason": "工程历史验证",
                     "issued_at": self.instant(second), "expires_at": self.instant(expires) if action == "approve" else None}
        with self.clock(second), open_store(self.db) as store:
            return record_human_decision(store, statement, self.verifier.proof(statement), verifier=self.verifier)

    def assess(self, second=50, *, as_of=None, verifier=True, pair=None):
        from ai_operator.review_history import assess_human_review_history
        capture, review = pair or (self.receipt, self.review)
        with self.clock(second), open_store(self.db, readonly=True) as store:
            return assess_human_review_history(store, capture["capture_event_id"], review["review_event_id"],
                self.instant(second) if as_of is None else as_of,
                verifier=self.verifier if verifier is True else verifier if verifier is not False else None)

    def test_pending_no_adapter_has_no_write_or_source_qualification(self):
        with open_store(self.db, readonly=True) as store:
            before = store.snapshot()
        result = self.assess(verifier=False)
        self.assertEqual(result["human_status"], "pending")
        self.assertEqual(result["status"], "blocked_pending_human_review")
        self.assertEqual(result["decision_event_ids"], [])
        self.assertIsNone(result["active_decision_event_id"])
        for name in ("source_verified", "historical_availability_verified", "training_eligible", "eligible", "production_eligible"):
            self.assertIs(result[name], False)
        self.assertGreaterEqual(len(result["remaining_gaps"]), 2)
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot(), before)

    def test_missing_and_mismatch_documents_have_priority(self):
        for evidence, expected in ((self.fixture.evidence(), "blocked_missing_evidence"),
                (self.fixture.matched_evidence(self.receipt, wrong_revision=True), "blocked_mismatch")):
            review = self.fixture.review(self.receipt, evidence)
            result = self.assess(pair=(self.receipt, review), verifier=False)
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["review_gate_status"], expected)
            self.assertEqual(result["human_status"], "pending")

    def test_adjusted_snapshot_keeps_factor_and_download_history_gaps(self):
        from ai_operator.capture_journal import capture_to_store
        for basis in ("qfq_snapshot", "hfq_snapshot"):
            with self.subTest(basis=basis), open_store(self.db) as store:
                capture = capture_to_store(store,
                    {"path": str(self.fixture.market), "source_id": "fixture-source", "code": "000001.SH",
                     "price_basis": basis, "revision": "fixture-v1"},
                    {"paths": [str(self.fixture.calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                     "revision": "fixture-v1", "coverage_start": "2026-01-05", "coverage_end": "2026-01-05"})
            review = self.fixture.review(capture, self.fixture.matched_evidence(capture))
            result = self.assess(pair=(capture, review), verifier=False)
            self.assertEqual(result["status"], "blocked_missing_evidence")
            self.assertEqual(len(result["remaining_gaps"]), 3)
            self.assertTrue(any("因子" in gap for gap in result["remaining_gaps"]))
            self.assertTrue(any("下载" in gap for gap in result["remaining_gaps"]))
            self.assertTrue(any("历史" in gap for gap in result["remaining_gaps"]))

    def test_future_naive_and_invalid_asof_rejected(self):
        for value in (self.instant(100), "2026-01-05T00:00:00", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess(second=50, as_of=value if value is not None else "not-a-time")

    def test_capture_review_cross_pair_rejected(self):
        second_capture = self.fixture.capture()
        with self.assertRaises(ValueError):
            self.assess(pair=(second_capture, self.review))

    def test_approved_still_has_all_qualification_gaps(self):
        approved = self.record("approve", 1)
        result = self.assess()
        self.assertEqual(result["human_status"], "approved")
        self.assertEqual(result["status"], "blocked_provenance_gaps")
        self.assertEqual(result["decision_event_ids"], [approved["decision_event_id"]])
        self.assertEqual(result["active_decision_event_id"], approved["decision_event_id"])
        self.assertIs(result["source_verified"], False)
        self.assertIs(result["historical_availability_verified"], False)
        self.assertIs(result["training_eligible"], False)
        self.assertIs(result["eligible"], False)
        self.assertIs(result["production_eligible"], False)

    def test_historical_approval_uses_original_authorized_receipt_time(self):
        valid_from, valid_until = self.instant(0), self.instant(10)

        class ExpiringAuthorityVerifier(SignedFixtureVerifier):
            def verify(self, statement, proof, *, received_at):
                instant = normalize_time(received_at)
                if not valid_from <= instant < valid_until:
                    raise ValueError("接收时审核授权已失效")
                return super().verify(statement, proof, received_at=received_at)

        self.verifier = ExpiringAuthorityVerifier()
        approval = self.record("approve", 1, expires=100)
        result = self.assess(second=50)
        self.assertEqual(result["human_status"], "approved")
        self.assertEqual(result["active_decision_event_id"], approval["decision_event_id"])
        self.assertEqual(result["status"], "blocked_provenance_gaps")
        self.assertIs(result["eligible"], False)

    def test_visible_events_without_adapter_are_unverified(self):
        approved = self.record("approve", 1)
        result = self.assess(verifier=False)
        self.assertEqual(result["human_status"], "unverified")
        self.assertEqual(result["status"], "blocked_authentication_unconfigured")
        self.assertEqual(result["decision_event_ids"], [approved["decision_event_id"]])
        self.assertIsNone(result["active_decision_event_id"])

    def test_rejection_with_document_gap_preserves_gap_priority(self):
        missing_review = self.fixture.review(self.receipt)
        self.record("reject", 1, pair=(self.receipt, missing_review))
        for verifier, expected_human in ((True, "rejected"), (False, "unverified")):
            result = self.assess(pair=(self.receipt, missing_review), verifier=verifier)
            self.assertEqual(result["human_status"], expected_human)
            self.assertEqual(result["status"], "blocked_missing_evidence")

    def test_legal_later_event_is_invisible_to_earlier_asof(self):
        self.record("approve", 20)
        for verifier in (True, False):
            result = self.assess(second=50, as_of=self.instant(10), verifier=verifier)
            self.assertEqual(result["human_status"], "pending")
            self.assertEqual(result["decision_event_ids"], [])

    def test_record_arriving_between_query_start_and_snapshot_is_invisible(self):
        from ai_operator.review_history import assess_human_review_history
        self.record("approve", 60)
        readings = iter([self.instant(50)])

        def advancing_now():
            return next(readings, self.instant(70))

        with self.clock(70), patch("ai_operator.contracts.utc_now", side_effect=advancing_now), \
                open_store(self.db, readonly=True) as store:
            result = assess_human_review_history(store, self.receipt["capture_event_id"],
                self.review["review_event_id"], self.instant(50), verifier=self.verifier)
        self.assertEqual(result["human_status"], "pending")
        self.assertEqual(result["decision_event_ids"], [])

    def test_recorded_after_received_is_invisible_at_received(self):
        approved = self.record("approve", 1)
        with open_store(self.db) as store:
            store._conn.execute("UPDATE events SET recorded_at=? WHERE event_id=?", (self.instant(20), approved["decision_event_id"]))
        result = self.assess(as_of=self.instant(10))
        self.assertEqual(result["human_status"], "pending")
        result = self.assess(as_of=self.instant(20))
        self.assertEqual(result["human_status"], "approved")

    def test_reject_latest_cannot_fall_back_to_old_approval(self):
        self.record("approve", 1)
        rejected = self.record("reject", 2)
        result = self.assess()
        self.assertEqual(result["human_status"], "rejected")
        self.assertEqual(result["status"], "blocked_human_rejected")
        self.assertEqual(result["active_decision_event_id"], rejected["decision_event_id"])

    def test_revoking_old_approval_does_not_revoke_new_approval(self):
        old = self.record("approve", 1)
        new = self.record("approve", 2)
        self.record("revoke", 3, target=old["decision_event_id"])
        result = self.assess()
        self.assertEqual(result["human_status"], "approved")
        self.assertEqual(result["active_decision_event_id"], new["decision_event_id"])
        self.record("revoke", 4, target=new["decision_event_id"])
        result = self.assess()
        self.assertEqual(result["human_status"], "revoked")
        self.assertEqual(result["status"], "blocked_human_revoked")
        self.assertEqual(result["active_decision_event_id"], new["decision_event_id"])

    def test_expired_latest_never_falls_back_to_old_approval(self):
        self.record("approve", 1, expires=200)
        latest = self.record("approve", 2, expires=20)
        self.assertEqual(self.assess(as_of=self.instant(19))["human_status"], "approved")
        result = self.assess(as_of=self.instant(20))
        self.assertEqual(result["human_status"], "expired")
        self.assertEqual(result["status"], "blocked_human_expired")
        self.assertEqual(result["active_decision_event_id"], latest["decision_event_id"])

    def test_same_effective_time_is_conflict_without_hash_order(self):
        first = self.record("approve", 1)
        second = self.record("reject", 1)
        result = self.assess()
        self.assertEqual(result["human_status"], "conflict")
        self.assertEqual(result["status"], "blocked_human_conflict")
        self.assertCountEqual(result["decision_event_ids"], [first["decision_event_id"], second["decision_event_id"]])
        self.assertIsNone(result["active_decision_event_id"])

    def test_legal_other_pair_does_not_affect_selected_pair(self):
        other_capture = self.fixture.capture()
        other_review = self.fixture.review(other_capture, self.fixture.matched_evidence(other_capture))
        self.record("approve", 1, pair=(other_capture, other_review))
        for verifier in (True, False):
            result = self.assess(verifier=verifier)
            self.assertEqual(result["human_status"], "pending")
            self.assertEqual(result["decision_event_ids"], [])

    def test_other_pair_authority_outside_current_adapter_remains_pending(self):
        other_capture = self.fixture.capture()
        other_review = self.fixture.review(other_capture, self.fixture.matched_evidence(other_capture))

        class OtherAuthorityVerifier(SignedFixtureVerifier):
            key = b"separate-engineering-authority-only"
            principal = {"principal_id": "other-reviewer", "authority_id": "other-authority",
                         "policy_id": "other-policy", "role": "source_reviewer"}

        self.verifier = OtherAuthorityVerifier()
        self.record("approve", 1, pair=(other_capture, other_review))
        self.verifier = SignedFixtureVerifier()
        result = self.assess()
        self.assertEqual(result["human_status"], "pending")
        self.assertEqual(result["decision_event_ids"], [])

    def test_invisible_related_proof_outside_adapter_scope_remains_pending(self):
        class OtherAuthorityVerifier(SignedFixtureVerifier):
            key = b"separate-later-engineering-authority-only"
            principal = {"principal_id": "later-reviewer", "authority_id": "later-authority",
                         "policy_id": "later-policy", "role": "source_reviewer"}

        self.verifier = OtherAuthorityVerifier()
        self.record("approve", 10)
        self.verifier = SignedFixtureVerifier()
        result = self.assess(as_of=self.instant(5))
        self.assertEqual(result["human_status"], "pending")
        self.assertEqual(result["decision_event_ids"], [])
        with self.assertRaises(ValueError):
            self.assess(as_of=self.instant(10))

    def test_tampered_related_event_is_rejected_with_or_without_adapter(self):
        approved = self.record("approve", 1)
        with open_store(self.db) as store:
            event = store.get_event(approved["decision_event_id"])
            event["payload"]["statement"]["reason"] = "篡改"
            store._conn.execute("UPDATE events SET event_json=? WHERE event_id=?",
                (canonical_json({key: event[key] for key in EVENT_FIELDS}), approved["decision_event_id"]))
        for verifier in (True, False):
            with self.subTest(verifier=verifier), self.assertRaises(ValueError):
                self.assess(verifier=verifier)

    def test_rehashed_changed_statement_cannot_reuse_signature(self):
        approved = self.record("approve", 1)
        with self.clock(3), open_store(self.db) as store:
            event = store.get_event(approved["decision_event_id"])
            body = {key: copy.deepcopy(event[key]) for key in EVENT_FIELDS}
            body["payload"]["statement"]["reason"] = "重哈希后的伪声明"
            body["source_id"] = hashlib.sha256(canonical_json(body["payload"]["statement"]).encode("utf-8")).hexdigest()
            store.append_event(body)
        with self.assertRaises(ValueError):
            self.assess()

    def test_illegal_recorded_time_cannot_be_silently_skipped(self):
        approved = self.record("approve", 1)
        for value in (self.instant(0), self.instant(100), "2026-01-05T00:00:00"):
            with open_store(self.db) as store:
                store._conn.execute("UPDATE events SET recorded_at=? WHERE event_id=?", (value, approved["decision_event_id"]))
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess()

    def test_query_over_ten_thousand_candidates_refuses_partial_history(self):
        with open_store(self.db) as store:
            with store._transaction():
                store._conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [
                    (format(index, "064x"), "0" * 64, "{}", "source.human_decision", "shadow", self.instant(1), self.instant(1))
                    for index in range(10001)])
        with self.assertRaisesRegex(ValueError, "10000|候选"):
            self.assess(verifier=False)

    def test_readonly_transaction_snapshot_excludes_concurrent_append(self):
        first = self.record("approve", 1)
        owner = self

        class ConcurrentVerifier(SignedFixtureVerifier):
            appended = False

            def verify(self, statement, proof, *, received_at):
                if not self.appended:
                    self.appended = True
                    owner.record("reject", 2)
                return super().verify(statement, proof, received_at=received_at)

        result = self.assess(verifier=ConcurrentVerifier())
        self.assertEqual(result["human_status"], "approved")
        self.assertEqual(result["decision_event_ids"], [first["decision_event_id"]])
        self.assertEqual(self.assess()["human_status"], "rejected")


if __name__ == "__main__":
    unittest.main()
