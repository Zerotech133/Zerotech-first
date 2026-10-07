"""核对日历只读命令的真实计算、错误拒绝及文件不变。"""

import copy
import hashlib
import json
import unittest

from tests_ai_operator import test_cli, test_calendar_reference


class CalendarCLITests(unittest.TestCase):
    setUp = test_cli.CLITests.setUp
    run_cli = test_cli.CLITests.run_cli
    def request(self, value):
        path = self.directory / "input.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def reference_request(self):
        return {"documents": test_calendar_reference.CalendarReferenceTests().documents(),
                "coverage_start": "2025-01-01", "coverage_end": "2025-02-09"}

    def test_build_reference_cli_without_database_or_input_mutation(self):
        path = self.request(self.reference_request())
        before = path.read_bytes()
        result = self.run_cli("calendar-reference", "--input", path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(len(value["days"]), 40)
        self.assertIs(value["production_eligible"], False)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_reference_cli_strict_fields_and_unknown_closure(self):
        value = self.reference_request()
        bad = copy.deepcopy(value)
        bad["trusted"] = True
        for request in (bad, {"documents": value["documents"]}):
            result = self.run_cli("calendar-reference", "--input", self.request(request))
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["ok"])
        raw = value["documents"][1]["raw_utf8"].replace("</article>", "<p>1月10日休市。</p></article>")
        value["documents"][1].update(raw_utf8=raw, sha256=hashlib.sha256(raw.encode()).hexdigest())
        result = self.run_cli("calendar-reference", "--input", self.request(value))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_compare_cli_match_mismatch_and_missing_days(self):
        fixture = test_calendar_reference.CalendarReferenceTests()
        reference = fixture.build()
        for capture, status, count in ((fixture.capture(), "match", 40),
                (fixture.capture(changed="2025-02-03"), "mismatch", 40),
                (fixture.capture(start="2025-01-02"), "incomplete", 39)):
            path = self.request({"reference": reference, "calendar_capture": capture})
            before = path.read_bytes()
            result = self.run_cli("calendar-compare", "--input", path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual((value["status"], value["compared_days"]), (status, count))
            self.assertIs(value["training_eligible"], False)
            self.assertEqual(path.read_bytes(), before)

    def test_compare_cli_rejects_rehashed_semantic_forgery(self):
        fixture = test_calendar_reference.CalendarReferenceTests()
        reference = fixture.build()
        reference["days"][0]["is_open"] = 1
        fixture.rehash(reference)
        result = self.run_cli("calendar-compare", "--input", self.request(
            {"reference": reference, "calendar_capture": fixture.capture()}))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_new_commands_reject_duplicate_json_without_side_effect(self):
        path = self.directory / "input.json"
        path.write_text('{"documents":[],"documents":[]}', encoding="utf-8")
        for command in ("calendar-reference", "calendar-compare"):
            result = self.run_cli(command, "--input", path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("重复键", json.loads(result.stdout)["error"]["message"])
        self.assertEqual(list(self.directory.iterdir()), [path])
