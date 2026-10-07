"""验证声明证据对照、人审限制及离线重验。"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from ai_operator.contracts import canonical_json, normalize_time, utc_now


class SourceReviewApiTests(unittest.TestCase):
    def test_public_api_available(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.source_review"),
                             "缺少冻结的来源证据审核接口")


class SourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def receipt(self, basis="raw", exchange="SSE"):
        from ai_operator.capture_journal import capture_to_store
        from ai_operator.memory import open_store
        market = self.directory / "market.json"
        calendar = self.directory / "calendar.json"
        market.write_text(json.dumps({"bars": [
            {"session": "2026-01-05", "open": 10, "high": 11, "low": 9,
             "close": 10, "volume": 1}]}), encoding="utf-8")
        calendar.write_text(json.dumps({"rows": [
            {"exchange": exchange, "cal_date": "20260105", "is_open": 1},
            {"exchange": exchange, "cal_date": "20260106", "is_open": 0}]}), encoding="utf-8")
        with open_store(self.directory / "memory.sqlite3") as store:
            return capture_to_store(store,
                {"path": str(market), "source_id": "fixture-price", "code": "000001.SH" if exchange == "SSE" else "000001.SZ",
                 "price_basis": basis, "revision": "fixture-v1"},
                {"paths": [str(calendar)], "exchange": exchange, "source_id": "fixture-calendar",
                 "revision": "fixture-v1", "coverage_start": "2026-01-05", "coverage_end": "2026-01-06"})

    def evidence(self, receipt):
        snapshot = receipt["snapshot"]
        exchange = receipt["calendar_capture"]["exchange"]
        documents = {
            "supplier_identity": {"source_id": snapshot["source_id"], "provider": "工程供应商"},
            "price_basis": {"code": snapshot["code"], "price_basis": snapshot["price_basis"], "adjustment_reference": None},
            "revision": {"revision": snapshot["revision"], "source_sha256": snapshot["source_file"]["sha256"]},
            "official_calendar": {"exchange": exchange, "rows": [
                {"cal_date": "2026-01-05", "is_open": 1}, {"cal_date": "2026-01-06", "is_open": 0}]},
        }
        evidence = {}
        for kind, document in documents.items():
            path = self.directory / (kind + ".json")
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            url = "https://www.sse.com.cn/calendar" if exchange == "SSE" else "https://www.szse.cn/calendar"
            evidence[kind] = {"path": str(path), "url": url if kind == "official_calendar" else "https://provider.example/evidence"}
        return evidence

    def document(self, evidence, kind, change):
        path = Path(evidence[kind]["path"])
        document = json.loads(path.read_text(encoding="utf-8"))
        change(document)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def build(self, receipt, evidence, reviewer="工程审核员"):
        from ai_operator.source_review import build_source_review
        return build_source_review(receipt, reviewer, evidence)

    def validate(self, review, receipt):
        from ai_operator.source_review import validate_source_review
        return validate_source_review(review, receipt)

    @staticmethod
    def rehash(review):
        review["review_id"] = hashlib.sha256(canonical_json(
            {key: value for key, value in review.items() if key != "review_id"}).encode("utf-8")).hexdigest()

    def test_empty_evidence_reports_four_missing_checks(self):
        receipt = self.receipt()
        review = self.build(receipt, {kind: None for kind in
            ("supplier_identity", "price_basis", "revision", "official_calendar")})
        self.assertEqual(review["status"], "needs_evidence")
        self.assertEqual(review["evidence"], [])
        self.assertEqual([check["name"] for check in review["checks"]],
                         ["supplier_identity", "price_basis", "revision", "official_calendar"])
        self.assertEqual([check["status"] for check in review["checks"]], ["missing"] * 4)
        self.assertIs(review["eligible"], False)
        self.assertIs(review["production_eligible"], False)
        self.assertEqual(self.validate(review, receipt), review)

    def test_matching_documents_remain_pending_human_review(self):
        receipt = self.receipt()
        before = copy.deepcopy(receipt)
        review = self.build(receipt, self.evidence(receipt))
        self.assertEqual(review["status"], "ready_for_human_review")
        self.assertEqual([check["status"] for check in review["checks"]], ["pass"] * 4)
        self.assertIs(review["eligible"], False)
        self.assertIs(review["production_eligible"], False)
        self.assertGreaterEqual(review["compiled_at"], receipt["recorded_available_at"])
        self.assertLessEqual(review["compiled_at"], utc_now())
        self.assertEqual(receipt, before)
        validated = self.validate(review, receipt)
        validated["checks"].clear()
        self.assertEqual(len(review["checks"]), 4)

    def test_offline_validation_preserves_raw_byte_binding(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        review = self.build(receipt, evidence)
        for item in review["evidence"]:
            raw = Path(item["path"]).read_bytes()
            self.assertEqual(item["raw_utf8"].encode("utf-8"), raw)
            self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item["size_bytes"], len(raw))
            Path(item["path"]).unlink()
        self.assertEqual(self.validate(review, receipt), review)

    def test_supplier_security_basis_revision_and_sourcehash_mismatch(self):
        for kind, field, value in (("supplier_identity", "source_id", "other-source"),
                ("price_basis", "code", "600000.SH"), ("price_basis", "price_basis", "qfq_snapshot"),
                ("revision", "revision", "other-revision"), ("revision", "source_sha256", "0" * 64)):
            with self.subTest(kind=kind, field=field):
                receipt = self.receipt()
                evidence = self.evidence(receipt)
                self.document(evidence, kind, lambda doc: doc.update({field: value}))
                review = self.build(receipt, evidence)
                self.assertEqual(review["status"], "mismatch")
                self.assertEqual(next(check for check in review["checks"] if check["name"] == kind)["status"], "fail")
                self.assertEqual(self.validate(review, receipt), review)

    def test_calendar_missing_day_and_closed_day_mismatch(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        self.document(evidence, "official_calendar", lambda doc: doc["rows"].pop())
        review = self.build(receipt, evidence)
        self.assertEqual(review["checks"][-1]["status"], "missing")
        self.assertEqual(review["status"], "needs_evidence")
        evidence = self.evidence(receipt)
        self.document(evidence, "official_calendar", lambda doc: doc["rows"][1].update(is_open=1))
        review = self.build(receipt, evidence)
        self.assertEqual(review["checks"][-1]["status"], "fail")
        self.assertEqual(review["status"], "mismatch")

    def test_fail_takes_precedence_over_missing(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        evidence["supplier_identity"] = None
        self.document(evidence, "revision", lambda doc: doc.update(revision="wrong"))
        self.assertEqual(self.build(receipt, evidence)["status"], "mismatch")

    def test_calendar_duplicate_same_value_and_extra_day_accepted(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        self.document(evidence, "official_calendar", lambda doc: doc["rows"].extend([
            {"cal_date": "2026-01-05", "is_open": 1}, {"cal_date": "2026-01-07", "is_open": 1}]))
        self.assertEqual(self.build(receipt, evidence)["status"], "ready_for_human_review")

    def test_calendar_conflicting_duplicate_rejected(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        self.document(evidence, "official_calendar", lambda doc: doc["rows"].append({"cal_date": "2026-01-05", "is_open": 0}))
        with self.assertRaises(ValueError):
            self.build(receipt, evidence)

    def test_official_domains_and_exchange_are_checked(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        for url in ("https://sse.com.cn.evil.example/a", "https://fakesse.com.cn/a",
                "https://sse.com.cn@evil.example/a", "https://www.szse.cn/a",
                "https://-bad.sse.com.cn/a", "https://a..sse.com.cn/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                evidence["official_calendar"]["url"] = url
                self.build(receipt, evidence)
        evidence["official_calendar"]["url"] = "https://notice.www.sse.com.cn/a"
        self.assertEqual(self.build(receipt, evidence)["status"], "ready_for_human_review")
        receipt = self.receipt(exchange="SZSE")
        self.assertEqual(self.build(receipt, self.evidence(receipt))["status"], "ready_for_human_review")

    def test_declared_urls_require_valid_https(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        for url in ("http://provider.example/a", "https:///a", "https://user:secret@provider.example/a",
                "https://provider.example:invalid/a", "https://provider.example:65536/a", "https://provider.example/a\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                evidence["supplier_identity"]["url"] = url
                self.build(receipt, evidence)

    def test_adjusted_basis_always_requires_adjustment_factors(self):
        for basis in ("qfq_snapshot", "hfq_snapshot"):
            with self.subTest(basis=basis):
                receipt = self.receipt(basis=basis)
                evidence = self.evidence(receipt)
                self.document(evidence, "price_basis", lambda doc: doc.update(adjustment_reference="https://provider.example/factors"))
                review = self.build(receipt, evidence)
                self.assertEqual([check["status"] for check in review["checks"]], ["pass"] * 4 + ["missing"])
                self.assertEqual(review["checks"][-1]["name"], "adjustment_factors")
                self.assertEqual(review["status"], "needs_evidence")
                self.assertEqual(self.validate(review, receipt), review)

    def test_raw_basis_rejects_adjustment_reference(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        self.document(evidence, "price_basis", lambda doc: doc.update(adjustment_reference="https://provider.example/factors"))
        with self.assertRaises(ValueError):
            self.build(receipt, evidence)

    def test_evidence_and_document_fields_types_and_constants_are_strict(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        for invalid in ({}, {**evidence, "unknown": None}, {**evidence, "revision": {}},
                {**evidence, "revision": {**evidence["revision"], "trusted": True}},
                {**evidence, "revision": False}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.build(receipt, invalid)
        cases = (("supplier_identity", b'{"source_id":"fixture-price","provider":"x","unknown":1}'),
                 ("supplier_identity", b'{"source_id":"fixture-price","source_id":"fixture-price","provider":"x"}'),
                 ("supplier_identity", b'{"source_id":"fixture-price","provider":NaN}'),
                 ("supplier_identity", b'{"source_id":"fixture-price","provider":1e999}'),
                 ("supplier_identity", b'{"source_id":"fixture-price","provider":true}'),
                 ("supplier_identity", b'\xff'),
                 ("official_calendar", b'{"exchange":"SSE","rows":[{"cal_date":"2026-01-05","is_open":true}]}'),
                 ("official_calendar", b'{"exchange":"SSE","rows":[{"cal_date":"2026-01-05T00:00:00Z","is_open":1}]}'),
                 ("official_calendar", b'{"exchange":"SSE","rows":[{"cal_date":"2026-02-30","is_open":1}]}'))
        for kind, raw in cases:
            with self.subTest(kind=kind, raw=raw), self.assertRaises(ValueError):
                evidence = self.evidence(receipt)
                Path(evidence[kind]["path"]).write_bytes(raw)
                self.build(receipt, evidence)

    def test_local_file_boundary_and_one_mib_limit(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        for path in (str(self.directory), "//server/share/file.json", "\\\\?\\C:\\evidence.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                evidence["supplier_identity"]["path"] = path
                self.build(receipt, evidence)
        evidence = self.evidence(receipt)
        Path(evidence["supplier_identity"]["path"]).write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.build(receipt, evidence)

    def test_reviewer_and_receipt_are_validated(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        for reviewer in ("", " ", None, True):
            with self.subTest(reviewer=reviewer), self.assertRaises(ValueError):
                self.build(receipt, evidence, reviewer)
        bad = copy.deepcopy(receipt)
        bad["snapshot"]["bars"][0]["close"] = 10.1
        with self.assertRaises(ValueError):
            self.build(bad, evidence)

    def test_validator_recomputes_false_pass_even_after_rehash(self):
        receipt = self.receipt()
        evidence = self.evidence(receipt)
        self.document(evidence, "revision", lambda doc: doc.update(revision="wrong"))
        review = self.build(receipt, evidence)
        review["checks"][2].update(status="pass", reason="声明一致")
        review["status"] = "ready_for_human_review"
        self.rehash(review)
        with self.assertRaises(ValueError):
            self.validate(review, receipt)

    def test_validator_rejects_rehashed_byte_hash_size_url_and_unknown_changes(self):
        receipt = self.receipt()
        review = self.build(receipt, self.evidence(receipt))
        for change in (lambda item: item["evidence"][0].update(raw_utf8="{}"),
                lambda item: item["evidence"][0].update(sha256="0" * 64),
                lambda item: item["evidence"][0].update(size_bytes=True),
                lambda item: item["evidence"][-1].update(url="https://evil.example/calendar"),
                lambda item: item.update(eligible=True), lambda item: item.update(production_eligible=True),
                lambda item: item.update(schema_version=True), lambda item: item.update(extra=1),
                lambda item: item["evidence"].append(copy.deepcopy(item["evidence"][0])),
                lambda item: item["checks"][0].update(extra=1),
                lambda item: item.update(capture_event_id="0" * 64),
                lambda item: item.update(stream_id="0" * 64)):
            bad = copy.deepcopy(review)
            change(bad)
            self.rehash(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.validate(bad, receipt)

    def test_compiled_time_rejects_naive_future_and_before_receipt(self):
        receipt = self.receipt()
        review = self.build(receipt, self.evidence(receipt))
        for value in ("2026-01-05T00:00:00", normalize_time("2099-01-01T00:00:00Z"), normalize_time("2026-01-05T00:00:00Z")):
            bad = copy.deepcopy(review)
            bad["compiled_at"] = value
            self.rehash(bad)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(bad, receipt)

    def test_reconstructed_documents_can_only_obtain_human_review_status(self):
        receipt = self.receipt()
        review = self.build(receipt, self.evidence(receipt))
        supplier = review["evidence"][0]
        document = json.loads(supplier["raw_utf8"])
        document["provider"] = "人为重造的声明"
        supplier["raw_utf8"] = json.dumps(document, ensure_ascii=False)
        raw = supplier["raw_utf8"].encode("utf-8")
        supplier["sha256"] = hashlib.sha256(raw).hexdigest()
        supplier["size_bytes"] = len(raw)
        self.rehash(review)
        validated = self.validate(review, receipt)
        self.assertEqual(validated["status"], "ready_for_human_review")
        self.assertIs(validated["eligible"], False)
        self.assertIs(validated["production_eligible"], False)


if __name__ == "__main__":
    unittest.main()
