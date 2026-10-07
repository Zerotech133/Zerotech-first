"""验证来源快照到标签接口的审计限制及交易所、日历身份绑定。"""

import copy
import json
from pathlib import Path
import tempfile
import unittest


class SourceInputsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def captures(self, *, exchange="SSE", closed=False, start="2026-01-05", revision="v1"):
        from ai_operator.provenance import capture_snapshot
        from ai_operator.trading_calendar import capture_calendar
        market = self.directory / "market.json"
        cal = self.directory / "calendar.json"
        market.write_text(json.dumps({"bars": [
            {"session": "2026-01-05", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0},
            {"session": "2026-01-06", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 2}]}), encoding="utf-8")
        cal.write_text(json.dumps({"rows": [
            {"exchange": exchange, "cal_date": "20260105", "is_open": 0 if closed else 1},
            {"exchange": exchange, "cal_date": "20260106", "is_open": 1}]}), encoding="utf-8")
        snapshot = capture_snapshot(str(market), source_id="fixture-price", code="000001.SH",
                                    price_basis="raw", revision=revision)
        calendar = capture_calendar([str(cal)], exchange=exchange, source_id="fixture-calendar",
                                    revision="v1", coverage_start=start, coverage_end="2026-01-06")
        return snapshot, calendar

    def adapt(self, snapshot, calendar):
        from ai_operator.source_inputs import snapshot_for_outcomes
        return snapshot_for_outcomes(snapshot, calendar)

    def test_compatible_schema_unknown_trust_and_input_frozen(self):
        snapshot, calendar = self.captures()
        before = copy.deepcopy((snapshot, calendar))
        result = self.adapt(snapshot, calendar)
        self.assertEqual(set(result), {"code", "period", "bars", "calendar", "source"})
        self.assertEqual(result["source"]["availability_status"], "unknown")
        self.assertEqual(result["source"]["price_basis"], "raw")
        self.assertEqual(len(result["source"]["source_hash"]), 64)
        self.assertEqual(result["bars"][0]["available_at"], snapshot["received_at"])
        self.assertEqual((snapshot, calendar), before)
        result["calendar"]["sessions"].clear()
        self.assertEqual((snapshot, calendar), before)

    def test_wrong_exchange_rejected(self):
        snapshot, calendar = self.captures(exchange="SZSE")
        with self.assertRaises(ValueError):
            self.adapt(snapshot, calendar)

    def test_closed_session_and_outside_coverage_rejected(self):
        for kwargs in ({"closed": True}, {"start": "2026-01-06"}):
            snapshot, calendar = self.captures(**kwargs)
            with self.assertRaises(ValueError):
                self.adapt(snapshot, calendar)

    def test_content_tampering_rejected(self):
        snapshot, calendar = self.captures()
        snapshot["bars"][0]["close"] = 10.1
        with self.assertRaises(ValueError):
            self.adapt(snapshot, calendar)
        snapshot, calendar = self.captures()
        calendar["calendar"]["sessions"][0]["close_at"] = "2026-01-05T14:00:00+08:00"
        with self.assertRaises(ValueError):
            self.adapt(snapshot, calendar)

    def test_revision_binds_source_hash(self):
        first = self.adapt(*self.captures())
        second = self.adapt(*self.captures(revision="v2"))
        self.assertNotEqual(first["source"]["source_hash"], second["source"]["source_hash"])
        self.assertNotEqual(first["source"]["source_id"], second["source"]["source_id"])

    def test_real_outcome_api_hides_target_without_source_review(self):
        from ai_operator.contracts import utc_now
        from ai_operator.outcomes import label_decision
        from ai_operator.shadow import build_fact_pack, make_shadow_decision
        adapted = self.adapt(*self.captures())
        pack = build_fact_pack(adapted["code"], adapted["period"], "2026-01-04T00:00:00Z",
                               [{"id": "fixture", "source": "engineering-fixture", "occurred_at": "2026-01-04T00:00:00Z",
                                 "available_at": "2026-01-04T00:00:00Z", "data": {"count": 1}}],
                               {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
        decision = make_shadow_decision(pack, "observe", ["fixture"], "工程夹具", "2026-01-07T00:00:00Z")
        result = label_decision(pack, decision, adapted["bars"], adapted["calendar"], adapted["source"],
                                utc_now(), horizons=[1])
        self.assertEqual(result["labels"][0]["status"], "audit_only")
        self.assertEqual(result["labels"][0]["reason"], "unknown_source")
        self.assertIsNone(result["labels"][0]["target"])

    def shadow_pair(self, code="000001.SH", period="1d", as_of=None):
        from ai_operator.contracts import utc_now
        from ai_operator.shadow import build_fact_pack, make_shadow_decision
        from datetime import datetime, timedelta
        now = utc_now() if as_of is None else as_of
        pack = build_fact_pack(code, period, now,
                               [{"id": "fixture", "source": "engineering-fixture", "occurred_at": now,
                                 "available_at": now, "data": {"count": 1}}],
                               {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
        decision = make_shadow_decision(pack, "observe", ["fixture"], "工程夹具",
                                        (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
        return pack, decision

    def test_captured_label_rejects_wrong_security_and_period(self):
        from ai_operator.contracts import utc_now
        from ai_operator.source_inputs import label_captured_decision
        snapshot, calendar = self.captures()
        for code, period in (("000001.SZ", "1d"), ("000001.SH", "5m")):
            pack, decision = self.shadow_pair(code, period)
            with self.subTest(code=code, period=period), self.assertRaises(ValueError):
                label_captured_decision(snapshot, calendar, pack, decision, utc_now(), horizons=[1])

    def test_captured_label_audit_and_receipt_time_guard(self):
        from ai_operator.contracts import utc_now
        from ai_operator.source_inputs import label_captured_decision
        snapshot, calendar = self.captures()
        pack, decision = self.shadow_pair()
        result = label_captured_decision(snapshot, calendar, pack, decision, utc_now(), horizons=[1])
        self.assertEqual(result["labels"][0]["status"], "audit_only")
        self.assertIsNone(result["labels"][0]["target"])
        # 决策早于评估，单靠第三批决策时点校验无法拦截早于当前接收的评估。
        pack, decision = self.shadow_pair(as_of="2026-01-05T06:00:00+00:00")
        with self.assertRaises(ValueError):
            label_captured_decision(snapshot, calendar, pack, decision, "2026-01-07T00:00:00Z", horizons=[1])

    def test_captured_label_rejects_decision_before_calendar_coverage(self):
        from ai_operator.contracts import utc_now
        from ai_operator.source_inputs import label_captured_decision
        snapshot, calendar = self.captures()
        pack, decision = self.shadow_pair(as_of="2025-01-02T06:00:00+00:00")
        with self.assertRaises(ValueError):
            label_captured_decision(snapshot, calendar, pack, decision, utc_now(), horizons=[1])

    def test_calendar_start_uses_chinese_local_date(self):
        from ai_operator.contracts import utc_now
        from ai_operator.source_inputs import label_captured_decision
        snapshot, calendar = self.captures()
        pack, decision = self.shadow_pair(as_of="2026-01-04T16:00:00+00:00")
        result = label_captured_decision(snapshot, calendar, pack, decision, utc_now(), horizons=[1])
        self.assertEqual(result["labels"][0]["reference_session"], "2026-01-05")

    def test_captured_label_rejects_invalid_pack_without_traceback(self):
        from ai_operator.contracts import utc_now
        from ai_operator.source_inputs import label_captured_decision
        snapshot, calendar = self.captures()
        for pack in (None, [], {}, {"code": "000001.SH", "period": "1d"}):
            with self.subTest(pack=pack), self.assertRaises(ValueError):
                label_captured_decision(snapshot, calendar, pack, {}, utc_now(), horizons=[1])


if __name__ == "__main__":
    unittest.main()
