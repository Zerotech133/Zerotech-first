"""验证持久接收、证据审核和放行查询的CLI契约与重开读取。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ai_operator.contracts import utc_now

ROOT = Path(__file__).resolve().parents[1]


class Phase5CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.request = self.directory / "request.json"
        self.db = self.directory / "audit.sqlite"
        self.market = self.directory / "market.json"
        self.calendar = self.directory / "calendar.json"
        self.market.write_text(json.dumps({"bars": [{"session": "2026-01-05", "open": 10, "high": 11,
                                                    "low": 9, "close": 10, "volume": 0}]}), encoding="utf-8")
        self.calendar.write_text(json.dumps({"rows": [{"exchange": "SSE", "cal_date": "20260105",
                                                       "is_open": 1}]}), encoding="utf-8")

    def run_cli(self, command, value=None):
        args = [sys.executable, "-B", "-m", "ai_operator", command, "--db", str(self.db)]
        if value is not None:
            self.request.write_text(json.dumps(value), encoding="utf-8")
            args.extend(["--input", str(self.request)])
        result = subprocess.run(args, cwd=self.directory, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                text=True, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def capture_request(self):
        return {"source_request": {"path": str(self.market), "source_id": "fixture-source", "code": "000001.SH",
                                   "price_basis": "raw", "revision": "fixture-v1"},
                "calendar_request": {"paths": [str(self.calendar)], "exchange": "SSE",
                                     "source_id": "fixture-calendar", "revision": "fixture-v1",
                                     "coverage_start": "2026-01-05", "coverage_end": "2026-01-05"}}

    def capture(self):
        self.assertEqual(self.run_cli("init")[0], 0)
        code, receipt = self.run_cli("capture-journal", self.capture_request())
        self.assertEqual(code, 0, receipt)
        return receipt

    def test_journal_history_and_review_gate_full_chain(self):
        first = self.capture()
        request = self.capture_request()
        request["previous_event_id"] = first["capture_event_id"]
        code, second = self.run_cli("capture-journal", request)
        self.assertEqual(code, 0, second)
        code, history = self.run_cli("capture-history", {"capture_event_id": second["capture_event_id"], "as_of": utc_now()})
        self.assertEqual(code, 0, history)
        self.assertEqual([entry["capture_event_id"] for entry in history],
                         [first["capture_event_id"], second["capture_event_id"]])
        code, review = self.run_cli("source-review", {"capture_event_id": second["capture_event_id"],
                                 "reviewer": "工程验收", "evidence": dict.fromkeys(
                                     ("supplier_identity", "price_basis", "revision", "official_calendar"))})
        self.assertEqual(code, 0, review)
        code, gate = self.run_cli("review-gate", {"capture_event_id": second["capture_event_id"],
                              "review_event_id": review["review_event_id"], "as_of": utc_now()})
        self.assertEqual(code, 0, gate)
        self.assertEqual(gate["status"], "blocked_missing_evidence")
        self.assertFalse(gate["eligible"])
        self.assertEqual(self.run_cli("snapshot")[1]["events"], 3)

    def test_capture_requires_existing_independent_database(self):
        code, result = self.run_cli("capture-journal", self.capture_request())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertFalse(self.db.exists())

    def test_rejects_injected_time_trust_and_unknown_request_fields(self):
        self.run_cli("init")
        for field in ("received_at", "available_at", "trusted", "snapshot"):
            request = self.capture_request()
            request["source_request"][field] = True
            code, result = self.run_cli("capture-journal", request)
            self.assertEqual(code, 2, result)
        for command in ("capture-journal", "capture-history", "source-review", "review-gate", "journal-outcome"):
            code, result = self.run_cli(command, {"unknown": True})
            self.assertEqual(code, 2, result)
            self.assertFalse(result["ok"])
        self.assertEqual(self.run_cli("snapshot")[1]["events"], 0)

    def test_missing_history_event_and_invalid_asof_are_json_errors(self):
        receipt = self.capture()
        for value in ({"capture_event_id": "a" * 64, "as_of": utc_now()},
                      {"capture_event_id": receipt["capture_event_id"], "as_of": "2026-10-07T00:00:00"}):
            code, result = self.run_cli("capture-history", value)
            self.assertEqual(code, 2)
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
