"""验证独立交易周/节假日正文解析和逐日描述性对照。"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ai_operator.contracts import canonical_json, utc_now
from ai_operator.trading_calendar import capture_calendar


class CalendarReferenceTests(unittest.TestCase):
    def document(self, kind, paragraphs, *, url=None, wrapper="article"):
        raw = "<" + wrapper + ">" + "".join("<p>" + paragraph + "</p>" for paragraph in paragraphs) + "</" + wrapper + ">"
        return {"kind": kind, "url": url or "https://www.sse.com.cn/" + kind + ".html",
                "raw_utf8": raw, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}

    def documents(self):
        return [self.document("week_rule", ["本所交易日为每周一至周五。国家法定假日和本所公告的休市日，本所市场休市。"]),
                self.document("annual_notice", ["上海证券交易所现将2025年部分节假日休市安排通知如下。",
                    "（一）元旦：1月1日（星期三）休市，1月2日（星期四）起照常开市。",
                    "（二）春节：1月28日（星期二）至2月4日（星期二）休市，2月5日（星期三）起照常开市。另外，1月26日（星期日）、2月8日（星期六）为周末休市。"])]

    def build(self, documents=None, start="2025-01-01", end="2025-02-09"):
        from ai_operator.calendar_reference import build_calendar_reference
        return build_calendar_reference(self.documents() if documents is None else documents, start, end)

    def validate(self, reference):
        from ai_operator.calendar_reference import validate_calendar_reference
        return validate_calendar_reference(reference)

    def compare(self, reference, capture):
        from ai_operator.calendar_reference import compare_calendar_reference
        return compare_calendar_reference(reference, capture)

    @staticmethod
    def rehash(reference):
        reference["reference_id"] = hashlib.sha256(canonical_json(
            {key: value for key, value in reference.items() if key != "reference_id"}).encode("utf-8")).hexdigest()

    def capture(self, start="2025-01-01", end="2025-02-09", *, changed=None, exchange="SSE"):
        from datetime import date, timedelta
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        days = []
        while first <= last:
            session = first.isoformat()
            is_open = int(first.weekday() < 5 and session != "2025-01-01" and not "2025-01-28" <= session <= "2025-02-04")
            if changed and session == changed:
                is_open = 1 - is_open
            days.append({"exchange": exchange, "cal_date": session, "is_open": is_open})
            first += timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.json"
            path.write_text(json.dumps({"rows": days}), encoding="utf-8")
            return capture_calendar([str(path)], exchange=exchange, source_id="engineering-local-calendar", revision="v1",
                                    coverage_start=start, coverage_end=end)

    def test_single_day_cross_month_holiday_and_no_working_weekend_open(self):
        reference = self.build()
        actual = {day["session"]: day["is_open"] for day in reference["days"]}
        self.assertEqual(len(reference["days"]), 40)
        for session, expected in (("2025-01-01", 0), ("2025-01-02", 1), ("2025-01-26", 0),
                ("2025-01-28", 0), ("2025-02-03", 0), ("2025-02-04", 0), ("2025-02-05", 1), ("2025-02-08", 0)):
            self.assertEqual(actual[session], expected)
        self.assertTrue(all(day["basis"] for day in reference["days"]))
        self.assertIs(reference["exception_completeness_verified"], False)
        self.assertIs(reference["source_authenticated"], False)
        self.assertIs(reference["production_eligible"], False)

    def test_reference_is_deep_copy_and_preserves_complete_raw_evidence(self):
        documents = self.documents()
        frozen = copy.deepcopy(documents)
        reference = self.build(documents)
        self.assertEqual(reference["documents"], frozen)
        documents[0]["raw_utf8"] = "changed"
        validated = self.validate(reference)
        self.assertEqual(validated, reference)
        validated["days"].clear()
        self.assertEqual(len(reference["days"]), 40)

    def test_no_week_rule_exception_or_target_year_is_rejected(self):
        cases = [self.documents()[1:], self.documents()[:1],
                 [self.document("week_rule", ["本所交易日为每周一至周五。"]), self.documents()[1]],
                 [self.document("week_rule", ["国家法定假日和本所公告的休市日，本所市场休市。"]), self.documents()[1]]]
        for documents in cases:
            with self.subTest(documents=documents), self.assertRaises(ValueError):
                self.build(documents)
        with self.assertRaises(ValueError):
            self.build(start="2025-12-31", end="2026-01-02")

    def test_week_rule_unknown_weekday_or_dated_closure_rejected(self):
        for extra in ("每周三休市。", "2025年1月10日休市。", "2025-01-10休市。", "2025/01/10休市。"):
            documents = self.documents()
            doc = documents[0]
            raw = doc["raw_utf8"].replace("</article>", "<p>" + extra + "</p></article>")
            doc.update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.build(documents)

    def test_weekend_boundary_holiday_allows_weekday_reopening(self):
        documents = [self.documents()[0], self.document("annual_notice", [
            "现将2025年部分节假日休市安排通知如下。",
            "节假日：1月4日（星期六）至1月5日（星期日）休市，1月6日（星期一）起照常开市。"])]
        reference = self.build(documents, start="2025-01-04", end="2025-01-06")
        self.assertEqual([day["is_open"] for day in reference["days"]], [0, 0, 1])

    def test_extracted_evidence_marker_matches_declared_document_type(self):
        for suffix, wrong_kind in (("docx", "pdf_extracted_text"), ("pdf", "docx_extracted_text")):
            documents = self.documents()
            doc = documents[0]
            raw = doc["raw_utf8"].replace("<article>", '<article data-evidence-kind="' + wrong_kind + '">')
            doc.update(url="https://www.sse.com.cn/rules." + suffix, raw_utf8=raw,
                       sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.build(documents)

    def test_title_navigation_script_and_comments_do_not_supply_body_rules(self):
        paragraph = self.documents()[0]["raw_utf8"]
        for raw in ("<title>" + paragraph + "</title><article><p>普通正文</p></article>",
                "<nav>" + paragraph + "</nav><article><p>普通正文</p></article>",
                "<article><script>" + paragraph + "</script><p>普通正文</p></article>",
                "<article><!--" + paragraph + "--><p>普通正文</p></article>"):
            documents = self.documents()
            documents[0].update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.build(documents)

    def test_wrong_weekday_invalid_date_wrong_year_and_reverse_interval_rejected(self):
        for text in ("元旦：1月1日（星期四）休市，1月2日（星期四）起照常开市。",
                "特殊节日：2月30日（星期日）休市，3月3日（星期一）起照常开市。",
                "特殊节日：2026年1月1日（星期四）休市，1月2日（星期四）起照常开市。",
                "春节：2月4日（星期二）至1月28日（星期二）休市，2月5日（星期三）起照常开市。"):
            documents = self.documents()
            documents[1] = self.document("annual_notice", ["现将2025年部分节假日休市安排通知如下。", text])
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.build(documents)

    def test_annual_year_must_be_in_body_and_unambiguous(self):
        for paragraphs in (["元旦：1月1日（星期三）休市，1月2日（星期四）起照常开市。"],
                ["现将2025年部分节假日休市安排通知如下。现将2026年部分节假日休市安排通知如下。",
                 "元旦：1月1日（星期三）休市，1月2日（星期四）起照常开市。"]):
            documents = [self.documents()[0], self.document("annual_notice", paragraphs)]
            with self.assertRaises(ValueError):
                self.build(documents)

    def test_unknown_closure_or_amendment_never_silently_ignored(self):
        for unknown in ("另安排1月10日停市，复市日期待定。", "调整春节休市安排，具体日期另行公布。",
                "1月10日休市。", "周六正常开市。", "元旦安排取消。", "部分交易日临时暂停交易。",
                "1月10日不交易。", "周六正常交易。"):
            documents = self.documents()
            doc = documents[1]
            raw = doc["raw_utf8"].replace("</article>", "<p>" + unknown + "</p></article>")
            doc.update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                self.build(documents)

    def test_visible_div_and_bare_body_clauses_cannot_be_ignored(self):
        for extra in ("<div>1月10日休市。</div>", "1月10日休市。"):
            documents = self.documents()
            doc = documents[1]
            raw = doc["raw_utf8"].replace("</article>", extra + "</article>")
            doc.update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.build(documents)

    def test_year_intro_cannot_hide_extra_dates_without_weekdays(self):
        documents = self.documents()
        doc = documents[1]
        raw = doc["raw_utf8"].replace("</article>",
            "<p>2025年部分节假日休市安排：1月10日休市，1月13日起照常开市。</p></article>")
        doc.update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
        with self.assertRaises(ValueError):
            self.build(documents)

    def test_publication_date_does_not_replace_explicit_notice_year(self):
        documents = self.documents()
        doc = documents[1]
        raw = doc["raw_utf8"].replace("<article>", "<article><p>2024年12月23日</p>")
        doc.update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
        reference = self.build(documents)
        self.assertEqual(reference["days"][0]["session"], "2025-01-01")

    def test_reopening_day_must_be_first_derived_open_day(self):
        for reopen in ("1月3日（星期五）", "1月4日（星期六）"):
            documents = self.documents()
            documents[1] = self.document("annual_notice", ["现将2025年部分节假日休市安排通知如下。",
                "元旦：1月1日（星期三）休市，" + reopen + "起照常开市。"])
            with self.subTest(reopen=reopen), self.assertRaises(ValueError):
                self.build(documents)

    def test_weekend_clause_cannot_close_weekday(self):
        documents = self.documents()
        documents[1] = self.document("annual_notice", ["现将2025年部分节假日休市安排通知如下。",
            "元旦：1月1日（星期三）休市，1月2日（星期四）起照常开市。另外，1月3日（星期五）为周末休市。"])
        with self.assertRaises(ValueError):
            self.build(documents)

    def test_compatible_supplement_adds_provenance_without_overwriting(self):
        documents = self.documents()
        documents.append(self.document("supplement", ["现将2025年春节休市安排通知如下。",
            "休市安排：1月28日（星期二）至2月4日（星期二）休市，2月5日（星期三）起照常开市。"],
            url="https://www.sse.com.cn/spring.html"))
        reference = self.build(documents)
        day = next(day for day in reference["days"] if day["session"] == "2025-02-03")
        self.assertGreaterEqual(len(day["basis"]), 3)

    def test_supplement_conflict_and_unknown_format_rejected(self):
        for text in ("春节：1月28日（星期二）至2月5日（星期三）休市，2月6日（星期四）起照常开市。",
                "2025年临时休市安排变更如下，1月10日起暂停交易。"):
            documents = self.documents()
            documents.append(self.document("supplement", ["现将2025年春节休市安排通知如下。", text],
                url="https://www.sse.com.cn/changed.html"))
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.build(documents)

    def test_duplicate_sources_url_or_raw_are_rejected(self):
        for duplicate in (copy.deepcopy(self.documents()[1]),
                {**self.documents()[1], "url": "https://www.sse.com.cn/duplicate.html"}):
            with self.assertRaises(ValueError):
                self.build(self.documents() + [duplicate])

    def test_documents_strict_fields_url_hash_types_and_size(self):
        for change in (lambda item: item.update(sha256="0" * 64), lambda item: item.update(extra=1),
                lambda item: item.update(kind=True), lambda item: item.update(raw_utf8=True),
                lambda item: item.update(url="https://sse.com.cn.evil.example/x"),
                lambda item: item.update(url="http://www.sse.com.cn/x"),
                lambda item: item.update(url="https://user:secret@www.sse.com.cn/x"),
                lambda item: item.update(raw_utf8=" " * (1024 * 1024 + 1)),
                lambda item: item.update(raw_utf8="\ud800")):
            documents = self.documents()
            change(documents[0])
            with self.assertRaises(ValueError):
                self.build(documents)
        for documents in ([], None, {}, (self.documents()[0],)):
            from ai_operator.calendar_reference import build_calendar_reference
            with self.assertRaises(ValueError):
                build_calendar_reference(documents, "2025-01-01", "2025-02-09")

    def test_rehashed_forged_days_clauses_and_flags_rejected(self):
        reference = self.build()
        for change in (lambda item: item["days"][0].update(is_open=1),
                lambda item: item["clauses"][1].update(start="2025-01-02"),
                lambda item: item["days"][0].update(is_open=False),
                lambda item: item.update(exception_completeness_verified=True),
                lambda item: item.update(source_authenticated=True), lambda item: item.update(production_eligible=True),
                lambda item: item.update(schema_version=True), lambda item: item.update(extra=1)):
            bad = copy.deepcopy(reference)
            change(bad)
            self.rehash(bad)
            with self.assertRaises(ValueError):
                self.validate(bad)

    def test_validate_preserves_compilation_time_and_rejects_future_naive(self):
        reference = self.build()
        self.assertEqual(self.validate(reference)["compiled_at"], reference["compiled_at"])
        self.assertLessEqual(reference["compiled_at"], utc_now())
        for value in ("2099-01-01T00:00:00.000000+00:00", "2025-01-01T00:00:00"):
            bad = copy.deepcopy(reference)
            bad["compiled_at"] = value
            self.rehash(bad)
            with self.assertRaises(ValueError):
                self.validate(bad)

    def test_compare_match_never_promotes_source_or_training(self):
        result = self.compare(self.build(), self.capture())
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["compared_days"], 40)
        self.assertEqual(result["differences"], [])
        self.assertEqual(result["missing_sessions"], [])
        for name in ("source_authenticated", "exception_completeness_verified", "source_verified",
                "historical_availability_verified", "training_eligible", "eligible", "production_eligible"):
            self.assertIs(result[name], False)

    def test_compare_mismatch_returns_date_expected_actual_and_basis(self):
        result = self.compare(self.build(), self.capture(changed="2025-02-03"))
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(len(result["differences"]), 1)
        difference = result["differences"][0]
        self.assertEqual((difference["session"], difference["expected"], difference["actual"]), ("2025-02-03", 0, 1))
        self.assertTrue(difference["basis"])

    def test_compare_smaller_capture_is_incomplete_and_extra_days_ignored(self):
        result = self.compare(self.build(), self.capture(start="2025-01-02", end="2025-02-08"))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["compared_days"], 38)
        self.assertEqual(result["missing_sessions"], ["2025-01-01", "2025-02-09"])
        self.assertEqual(self.compare(self.build(), self.capture(end="2025-02-10"))["compared_days"], 40)

    def test_compare_wrong_exchange_and_rehashed_bad_capture_rejected(self):
        with self.assertRaises(ValueError):
            self.compare(self.build(), self.capture(exchange="SZSE"))
        capture = self.capture()
        capture["days"][0]["is_open"] = True
        with self.assertRaises(ValueError):
            self.compare(self.build(), capture)


if __name__ == "__main__":
    unittest.main()
