"""验证来源与日历命令的真实文件读取、参数边界和只读行为。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Phase4CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.request = self.directory / "request.json"
        self.market = self.directory / "market.json"
        self.calendar = self.directory / "calendar.json"
        self.market.write_text(json.dumps({"bars": [
            {"session": "2026-01-05", "open": 10, "high": 11, "low": 9,
             "close": 10.5, "volume": 0}]}), encoding="utf-8")
        self.calendar.write_text(json.dumps({"rows": [
            {"exchange": "SSE", "cal_date": "20260105", "is_open": 1}]}), encoding="utf-8")

    def run_cli(self, command, value):
        self.request.write_text(json.dumps(value), encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        result = subprocess.run([sys.executable, "-B", "-m", "ai_operator", command,
                                 "--input", str(self.request)], cwd=self.directory,
                                env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                text=True, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(result.stderr, "")
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before)
        return result.returncode, json.loads(result.stdout)

    def source_request(self):
        return {"path": str(self.market), "source_id": "fixture-local", "code": "000001.SH",
                "price_basis": "raw", "revision": "fixture-v1"}

    def calendar_request(self):
        return {"paths": [str(self.calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                "revision": "fixture-v1", "coverage_start": "2026-01-05", "coverage_end": "2026-01-05"}

    def test_capture_source_default_format_and_readonly(self):
        code, result = self.run_cli("capture-source", self.source_request())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["kind"], "daily_local_capture")
        self.assertEqual(result["bars"][0]["available_at"], result["received_at"])
        self.assertFalse(result["production_eligible"])

    def test_capture_calendar_default_format_and_readonly(self):
        code, result = self.run_cli("capture-calendar", self.calendar_request())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["calendar"]["sessions"][0]["session"], "2026-01-05")
        self.assertFalse(result["production_eligible"])

    def test_source_inputs_full_cli_chain(self):
        code, snapshot = self.run_cli("capture-source", self.source_request())
        self.assertEqual(code, 0, snapshot)
        code, calendar = self.run_cli("capture-calendar", self.calendar_request())
        self.assertEqual(code, 0, calendar)
        code, result = self.run_cli("source-inputs", {"snapshot": snapshot, "calendar_capture": calendar})
        self.assertEqual(code, 0, result)
        self.assertEqual(result["source"]["availability_status"], "unknown")
        self.assertEqual(set(result["bars"][0]), {"session", "close", "close_at", "available_at"})

    def test_rejects_injected_time_and_trust_fields(self):
        for command, request in (("capture-source", self.source_request()),
                                 ("capture-calendar", self.calendar_request())):
            for field in ("received_at", "available_at", "availability_status"):
                with self.subTest(command=command, field=field):
                    code, result = self.run_cli(command, dict(request, **{field: "trusted"}))
                    self.assertEqual(code, 2)
                    self.assertFalse(result["ok"])

    def test_missing_files_and_request_fields_are_json_errors(self):
        request = self.source_request()
        request["path"] = str(self.directory / "missing.json")
        code, result = self.run_cli("capture-source", request)
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        for command in ("capture-source", "capture-calendar", "source-inputs"):
            code, result = self.run_cli(command, {})
            self.assertEqual(code, 2)
            self.assertFalse(result["ok"])

    def test_captured_outcome_identity_guard_and_audit(self):
        from ai_operator.contracts import utc_now
        from ai_operator.shadow import build_fact_pack, make_shadow_decision
        from datetime import datetime, timedelta
        code, snapshot = self.run_cli("capture-source", self.source_request())
        self.assertEqual(code, 0, snapshot)
        code, calendar = self.run_cli("capture-calendar", self.calendar_request())
        self.assertEqual(code, 0, calendar)
        for security, expected in (("000001.SH", 0), ("000001.SZ", 2)):
            now = utc_now()
            pack = build_fact_pack(security, "1d", now, [],
                                   {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
            decision = make_shadow_decision(pack, "observe", [], "工程夹具",
                                            (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
            code, result = self.run_cli("captured-outcome", {"snapshot": snapshot,
                                       "calendar_capture": calendar, "pack": pack, "decision": decision,
                                       "evaluated_at": utc_now(), "horizons": [1]})
            self.assertEqual(code, expected, result)
            if expected == 0:
                self.assertEqual(result["labels"][0]["status"], "audit_only")
                self.assertIsNone(result["labels"][0]["target"])


if __name__ == "__main__":
    unittest.main()
