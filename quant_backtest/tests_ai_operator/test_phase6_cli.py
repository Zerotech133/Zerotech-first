"""验证默认拒绝人工批准、只读历史与请求身份注入边界。"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ai_operator.capture_journal import capture_to_store
from ai_operator.contracts import utc_now
from ai_operator.memory import open_store
from ai_operator.review_gate import record_source_review


ROOT = Path(__file__).resolve().parents[1]


class Phase6CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.db = self.directory / "audit.sqlite"
        self.request = self.directory / "request.json"
        market = self.directory / "market.json"
        calendar = self.directory / "calendar.json"
        market.write_text(json.dumps({"bars": [{"session": "2026-01-05", "open": 10,
                          "high": 11, "low": 9, "close": 10, "volume": 0}]}), encoding="utf-8")
        calendar.write_text(json.dumps({"rows": [{"exchange": "SSE", "cal_date": "20260105",
                                                 "is_open": 1}]}), encoding="utf-8")
        with open_store(self.db) as store:
            self.receipt = capture_to_store(store,
                {"path": str(market), "source_id": "fixture-source", "code": "000001.SH",
                 "price_basis": "raw", "revision": "fixture-v1"},
                {"paths": [str(calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                 "revision": "fixture-v1", "coverage_start": "2026-01-05", "coverage_end": "2026-01-05"})
            self.review = record_source_review(store, self.receipt["capture_event_id"], "工程缺口登记",
                dict.fromkeys(("supplier_identity", "price_basis", "revision", "official_calendar")))

    def run_cli(self, command, value, *, db=None, raw=None):
        self.request.write_text(json.dumps(value) if raw is None else raw, encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-m", "ai_operator", command,
                "--db", str(self.db if db is None else db), "--input", str(self.request)],
                cwd=self.directory, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                text=True, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def history_request(self):
        return {"capture_event_id": self.receipt["capture_event_id"],
                "review_event_id": self.review["review_event_id"], "as_of": utc_now()}

    def decision_request(self):
        now = utc_now()
        return {"statement": {"schema_version": 1, "request_id": "a" * 32, "action": "approve",
            "capture_event_id": self.receipt["capture_event_id"],
            "review_event_id": self.review["review_event_id"], "target_decision_event_id": None,
            "reason": "工程请求，不能作为真人批准", "issued_at": now,
            "expires_at": (datetime.fromisoformat(now) + timedelta(days=1)).isoformat()},
            "proof": {"claimed_name": "老田"}}

    def test_history_missing_evidence_remains_pending_and_readonly(self):
        code, result = self.run_cli("review-history", self.history_request())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["human_status"], "pending")
        self.assertEqual(result["status"], "blocked_missing_evidence")
        for field in ("eligible", "production_eligible", "training_eligible", "source_verified",
                      "historical_availability_verified"):
            self.assertIs(result[field], False)
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot()["events"], 2)

    def test_no_authenticator_rejects_claimed_human_without_writing(self):
        code, result = self.run_cli("human-review", self.decision_request())
        self.assertEqual(code, 2)
        self.assertIn("认证适配器", result["error"]["message"])
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot()["events"], 2)

    def test_request_cannot_inject_authentication_or_trust(self):
        for field in ("verifier", "principal", "trusted", "policy", "authorized"):
            request = self.decision_request()
            request[field] = True
            code, result = self.run_cli("human-review", request)
            self.assertEqual(code, 2, result)
            self.assertIn("未知字段", result["error"]["message"])
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.snapshot()["events"], 2)

    def test_future_history_is_rejected(self):
        request = self.history_request()
        request["as_of"] = (datetime.fromisoformat(utc_now()) + timedelta(days=1)).isoformat()
        code, result = self.run_cli("review-history", request)
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])

    def test_uninitialized_database_is_not_created(self):
        absent = self.directory / "absent.sqlite"
        for command, request in (("human-review", self.decision_request()),
                                  ("review-history", self.history_request())):
            code, result = self.run_cli(command, request, db=absent)
            self.assertEqual(code, 2, result)
            self.assertFalse(absent.exists())

    def test_strict_request_json_and_identity_fields(self):
        code, result = self.run_cli("review-history", {}, raw='{"as_of":1,"as_of":2}')
        self.assertEqual(code, 2)
        self.assertIn("重复键", result["error"]["message"])
        request = self.history_request()
        request["principal"] = "self-declared"
        code, result = self.run_cli("review-history", request)
        self.assertEqual(code, 2)
        self.assertIn("未知字段", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
