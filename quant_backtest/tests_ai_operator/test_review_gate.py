"""验证库内来源审核的时点、绑定和拒绝放行，不自证可信来源。"""

import copy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_operator.contracts import canonical_json, utc_now
from ai_operator.memory import open_store
from ai_operator.shadow import build_fact_pack, make_shadow_decision


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.db = self.directory / "audit.sqlite"
        self.market = self.directory / "market.json"
        self.calendar = self.directory / "calendar.json"
        self.market.write_text(json.dumps({"bars": [
            {"session": "2026-01-05", "open": 10, "high": 11, "low": 9,
             "close": 10, "volume": 0}]}), encoding="utf-8")
        self.calendar.write_text(json.dumps({"rows": [
            {"exchange": "SSE", "cal_date": "20260105", "is_open": 1}]}), encoding="utf-8")
        with open_store(self.db):
            pass

    def capture(self):
        from ai_operator.capture_journal import capture_to_store
        with open_store(self.db) as store:
            return capture_to_store(store,
                                    {"path": str(self.market), "source_id": "fixture-source", "code": "000001.SH",
                                     "price_basis": "raw", "revision": "fixture-v1"},
                                    {"paths": [str(self.calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                                     "revision": "fixture-v1", "coverage_start": "2026-01-05",
                                     "coverage_end": "2026-01-05"})

    def evidence(self):
        return dict.fromkeys(("supplier_identity", "price_basis", "revision", "official_calendar"))

    def review(self, receipt, evidence=None):
        from ai_operator.review_gate import record_source_review
        with open_store(self.db) as store:
            return record_source_review(store, receipt["capture_event_id"], "工程验收",
                                        self.evidence() if evidence is None else evidence)

    def matched_evidence(self, receipt, *, wrong_revision=False):
        snapshot, calendar = receipt["snapshot"], receipt["calendar_capture"]
        documents = {"supplier_identity": {"source_id": snapshot["source_id"], "provider": "工程夹具"},
                     "price_basis": {"code": snapshot["code"], "price_basis": snapshot["price_basis"],
                                     "adjustment_reference": None},
                     "revision": {"revision": "wrong" if wrong_revision else snapshot["revision"],
                                  "source_sha256": snapshot["source_file"]["sha256"]},
                     "official_calendar": {"exchange": calendar["exchange"], "rows": [
                         {"cal_date": day["session"], "is_open": day["is_open"]} for day in calendar["days"]]}}
        result = {}
        for kind, document in documents.items():
            path = self.directory / (kind + "-工程夹具.json")
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            result[kind] = {"path": str(path), "url": "https://www.sse.com.cn/engineering-fixture"
                            if kind == "official_calendar" else "https://example.test/engineering-fixture"}
        return result

    def test_matching_documents_remain_blocked_for_human_review(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        review = self.review(receipt, self.matched_evidence(receipt))
        self.assertEqual(review["status"], "ready_for_human_review")
        with open_store(self.db, readonly=True) as store:
            gate = assess_review_gate(store, receipt["capture_event_id"], review["review_event_id"], utc_now())
        self.assertEqual(gate["status"], "blocked_pending_human_review")
        self.assertFalse(gate["eligible"])

    def test_mismatched_documents_block_and_preserve_reason(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        review = self.review(receipt, self.matched_evidence(receipt, wrong_revision=True))
        self.assertEqual(review["status"], "mismatch")
        with open_store(self.db, readonly=True) as store:
            gate = assess_review_gate(store, receipt["capture_event_id"], review["review_event_id"], utc_now())
        self.assertEqual(gate["status"], "blocked_mismatch")
        self.assertEqual(next(check for check in gate["checks"] if check["name"] == "revision")["status"], "fail")

    def test_reopen_and_gate_missing_evidence(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        review = self.review(receipt)
        self.assertFalse(review["eligible"])
        with open_store(self.db, readonly=True) as store:
            result = assess_review_gate(store, receipt["capture_event_id"], review["review_event_id"], utc_now())
            self.assertEqual(result["status"], "blocked_missing_evidence")
            self.assertTrue(all(check["status"] == "missing" for check in result["checks"]))
            self.assertFalse(result["eligible"])
            self.assertFalse(result["production_eligible"])
            self.assertEqual(store.snapshot()["events"], 2)

    def test_review_after_asof_is_not_visible(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        review = self.review(receipt)
        with open_store(self.db, readonly=True) as store, self.assertRaises(ValueError):
            assess_review_gate(store, receipt["capture_event_id"], review["review_event_id"],
                               receipt["recorded_available_at"])

    def test_review_recording_after_compile_not_visible_at_compile_time(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        base = datetime.fromisoformat(utc_now())
        recorded = (base + timedelta(seconds=2)).isoformat(timespec="microseconds")
        query_now = (base + timedelta(seconds=3)).isoformat(timespec="microseconds")
        # 显式模拟报告先生成、稍后落库，避免依赖Windows系统时钟分辨率。
        with patch("ai_operator.memory.utc_now", return_value=recorded), \
                patch("ai_operator.review_gate.utc_now", return_value=query_now):
            review = self.review(receipt)
            with open_store(self.db, readonly=True) as store:
                event = store.get_event(review["review_event_id"])
                compiled = event["payload"]["compiled_at"]
                self.assertGreater(event["recorded_at"], compiled)
                with self.assertRaisesRegex(ValueError, "尚未落库可用"):
                    assess_review_gate(store, receipt["capture_event_id"], review["review_event_id"], compiled)

    def test_journal_label_hash_binds_distinct_persistent_receipts(self):
        from ai_operator.review_gate import label_journal_decision
        first, second = self.capture(), self.capture()
        now = utc_now()
        pack = build_fact_pack("000001.SH", "1d", now, [],
                               {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
        decision = make_shadow_decision(pack, "observe", [], "工程夹具",
                                        (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
        with open_store(self.db, readonly=True) as store:
            left = label_journal_decision(store, first["capture_event_id"], pack, decision, utc_now(), [1])
            right = label_journal_decision(store, second["capture_event_id"], pack, decision, utc_now(), [1])
        self.assertNotEqual(left["source"]["source_hash"], right["source"]["source_hash"])

    def test_review_cannot_bind_another_capture(self):
        from ai_operator.review_gate import assess_review_gate
        first = self.capture()
        review = self.review(first)
        second = self.capture()
        with open_store(self.db, readonly=True) as store, self.assertRaises(ValueError):
            assess_review_gate(store, second["capture_event_id"], review["review_event_id"], utc_now())

    def test_wrong_kind_unknown_event_and_invalid_time_rejected(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        review = self.review(receipt)
        for review_id, as_of in ((receipt["capture_event_id"], utc_now()), ("a" * 64, utc_now()),
                                  (review["review_event_id"], "2026-10-07T15:00:00"),
                                  (review["review_event_id"], None)):
            with self.subTest(review_id=review_id, as_of=as_of), open_store(self.db, readonly=True) as store:
                with self.assertRaises(ValueError):
                    assess_review_gate(store, receipt["capture_event_id"], review_id, as_of)

    def test_rehashed_forged_pass_rejected(self):
        from ai_operator.review_gate import assess_review_gate
        receipt = self.capture()
        registered = self.review(receipt)
        with open_store(self.db) as store:
            event = store.get_event(registered["review_event_id"])
            payload = copy.deepcopy(event["payload"])
            payload["status"] = "ready_for_human_review"
            for check in payload["checks"]:
                check["status"] = "pass"
            payload["review_id"] = hashlib.sha256(canonical_json(
                {key: value for key, value in payload.items() if key != "review_id"}).encode("utf-8")).hexdigest()
            forged_id = store.append_event({key: (payload if key == "payload" else payload["review_id"]
                                                  if key == "source_id" else event[key])
                                           for key in ("schema_version", "kind", "environment", "source",
                                                       "source_id", "occurred_at", "available_at", "payload")})
        with open_store(self.db, readonly=True) as store, self.assertRaises(ValueError):
            assess_review_gate(store, receipt["capture_event_id"], forged_id, utc_now())

    def test_readonly_record_is_rejected(self):
        from ai_operator.review_gate import record_source_review
        receipt = self.capture()
        with open_store(self.db, readonly=True) as store, self.assertRaises(ValueError):
            record_source_review(store, receipt["capture_event_id"], "工程验收", self.evidence())
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot()["events"], 1)

    def test_journal_label_keeps_unknown_and_checks_code(self):
        from ai_operator.review_gate import label_journal_decision
        receipt = self.capture()
        now = utc_now()
        for security, valid in (("000001.SH", True), ("000001.SZ", False)):
            pack = build_fact_pack(security, "1d", now, [],
                                   {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
            decision = make_shadow_decision(pack, "observe", [], "工程夹具",
                                            (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
            with open_store(self.db, readonly=True) as store:
                if valid:
                    result = label_journal_decision(store, receipt["capture_event_id"], pack, decision, utc_now(), [1])
                    self.assertEqual(result["source"]["availability_status"], "unknown")
                    self.assertEqual(result["labels"][0]["status"], "audit_only")
                    self.assertIsNone(result["labels"][0]["target"])
                else:
                    with self.assertRaises(ValueError):
                        label_journal_decision(store, receipt["capture_event_id"], pack, decision, utc_now(), [1])


if __name__ == "__main__":
    unittest.main()
