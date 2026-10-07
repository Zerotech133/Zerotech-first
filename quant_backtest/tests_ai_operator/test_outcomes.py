"""未来收盘观察标签的交易日、可用时点和严格契约测试。"""

import copy
import hashlib
import unittest
from datetime import date, datetime, timedelta, timezone

from ai_operator.contracts import canonical_json, normalize_time
from ai_operator.outcomes import label_decision
from ai_operator.shadow import build_fact_pack, make_shadow_decision


def fixture(action="candidate_buy", as_of="2026-10-02T14:00:00+08:00", count=25):
    pack = build_fact_pack("000001", "1d", as_of,
                           [{"id": "fact", "source": "fixture", "occurred_at": as_of,
                             "available_at": as_of, "data": {"price": 10}}],
                           {"data": "d1", "algorithm": "a1", "knowledge": "k1", "rules": "r1"})
    decision = make_shadow_decision(pack, action, ["fact"], "影子观察",
                                    normalize_time(datetime.fromisoformat(as_of) + timedelta(hours=1)))
    sessions = []
    day = date(2026, 10, 2)
    while len(sessions) < count:
        if day.weekday() < 5:
            sessions.append({"session": day.isoformat(), "close_at": day.isoformat() + "T15:00:00+08:00"})
        day += timedelta(days=1)
    bars = [dict(item, close=100 + i, available_at=item["close_at"]) for i, item in enumerate(sessions)]
    source = {"source_id": "fixture", "source_hash": "a" * 64,
              "price_basis": "raw", "availability_status": "trusted"}
    return {"pack": pack, "decision": decision, "bars": bars,
            "calendar": {"version": "fixture-v1", "sessions": sessions}, "source": source,
            "evaluated_at": "2026-12-01T00:00:00+00:00"}


def rehash(value, field):
    value[field] = hashlib.sha256(canonical_json({k: v for k, v in value.items() if k != field}).encode("utf-8")).hexdigest()
    return value


class OutcomeTests(unittest.TestCase):
    def label(self, value=None, horizons=None):
        return label_decision(**(fixture() if value is None else value), horizons=horizons)

    def assert_hidden(self, label):
        for field in ["reference_close", "end_close", "target", "label_available_at"]:
            self.assertIsNone(label[field], field)

    def test_weekend_h5_uses_explicit_calendar_position_and_default_h20(self):
        result = self.label()
        self.assertEqual([item["horizon"] for item in result["labels"]], [5, 20])
        first = result["labels"][0]
        self.assertEqual(first["reference_session"], "2026-10-02")
        self.assertEqual(first["label_end_session"], "2026-10-09")
        self.assertEqual(first["status"], "matured")
        self.assertIsNone(first["reason"])
        self.assertEqual(first["reference_close"], 100)
        self.assertEqual(first["end_close"], 105)
        self.assertAlmostEqual(first["target"], 0.05)
        self.assertEqual(first["target_kind"], "forward_close_return")
        self.assertEqual(first["label_end_at"], "2026-10-09T07:00:00.000000+00:00")

    def test_holiday_is_excluded_only_by_calendar(self):
        value = fixture()
        value["calendar"]["sessions"].pop(2)
        value["bars"].pop(2)
        self.assertEqual(self.label(value, [5])["labels"][0]["label_end_session"], "2026-10-12")

    def test_reference_strictly_after_decision_instant(self):
        value = fixture(as_of="2026-10-02T15:00:00+08:00")
        label = self.label(value, [1])["labels"][0]
        self.assertEqual(label["reference_session"], "2026-10-05")
        self.assertEqual(label["label_end_session"], "2026-10-06")

    def test_any_missing_window_bar_does_not_skip_to_later_bar(self):
        for index in [0, 2, 5]:
            value = fixture()
            value["bars"].pop(index)
            with self.subTest(index=index):
                label = self.label(value, [5])["labels"][0]
                self.assertEqual((label["status"], label["reason"]), ("pending", "missing_bar"))
                self.assertEqual(label["label_end_session"], "2026-10-09")
                self.assert_hidden(label)

    def test_unknown_source_and_null_window_availability_are_audit_only(self):
        for index in [None, 0, 2, 5]:
            value = fixture()
            if index is None:
                value["source"]["availability_status"] = "unknown"
            else:
                value["bars"][index]["available_at"] = None
            with self.subTest(index=index):
                label = self.label(value, [5])["labels"][0]
                self.assertEqual(label["status"], "audit_only")
                self.assertIn(label["reason"], {"unknown_source", "unknown_availability"})
                self.assert_hidden(label)

    def test_audit_only_precedes_incomplete_calendar_and_missing_bar(self):
        value = fixture(count=3)
        value["bars"][0]["available_at"] = None
        value["bars"].pop(1)
        label = self.label(value, [5])["labels"][0]
        self.assertEqual(label["status"], "audit_only")
        self.assertIsNone(label["label_end_at"])
        self.assert_hidden(label)

    def test_future_prices_hidden_until_entire_window_available(self):
        value = fixture()
        value["evaluated_at"] = "2026-10-09T07:00:00+00:00"
        value["bars"][2]["available_at"] = "2026-10-09T08:00:00+00:00"
        label = self.label(value, [5])["labels"][0]
        self.assertEqual((label["status"], label["reason"]), ("pending", "not_available"))
        self.assert_hidden(label)
        value["evaluated_at"] = "2026-10-09T08:00:00+00:00"
        matured = self.label(value, [5])["labels"][0]
        self.assertEqual(matured["status"], "matured")
        self.assertEqual(matured["label_available_at"], "2026-10-09T08:00:00.000000+00:00")

    def test_batch_bars_can_share_delayed_available_time(self):
        value = fixture()
        for bar in value["bars"][:6]:
            bar["available_at"] = "2026-10-10T08:00:00+08:00"
        result = self.label(value, [5])["labels"][0]
        self.assertEqual(result["status"], "matured")
        self.assertEqual(result["label_available_at"], "2026-10-10T00:00:00.000000+00:00")

    def test_calendar_incomplete_reference_and_endpoint(self):
        for value in [fixture(count=2), fixture(as_of="2026-12-01T00:00:00+00:00")]:
            with self.subTest(value=value):
                label = self.label(value, [5])["labels"][0]
                self.assertEqual((label["status"], label["reason"]), ("pending", "calendar_incomplete"))
                self.assertIsNone(label["label_end_at"])
                self.assert_hidden(label)

    def test_unavailable_bars_outside_window_do_not_block(self):
        value = fixture()
        value["bars"][6]["available_at"] = None
        self.assertEqual(self.label(value, [5])["labels"][0]["status"], "matured")

    def test_all_actions_observe_identical_price_return_without_direction_flip(self):
        for action in ["candidate_buy", "candidate_sell", "observe", "abstain"]:
            with self.subTest(action=action):
                result = self.label(fixture(action=action), [5])
                self.assertEqual(result["action"], action)
                self.assertAlmostEqual(result["labels"][0]["target"], 0.05)

    def test_output_schema_hash_repeatability_and_input_isolation(self):
        value = fixture()
        snapshot = copy.deepcopy(value)
        result = self.label(value, [5])
        self.assertEqual(value, snapshot)
        self.assertEqual(result, self.label(value, [5]))
        self.assertEqual(set(result), {"schema_version", "decision_id", "fact_hash", "code", "action", "environment",
                                       "evaluated_at", "source", "calendar_version", "labels", "production_eligible", "outcome_id"})
        self.assertEqual(set(result["labels"][0]), {"horizon", "status", "reason", "reference_session", "label_end_session",
                                                   "label_end_at", "label_available_at", "reference_close", "end_close", "target", "target_kind"})
        self.assertEqual(result["outcome_id"], rehash(copy.deepcopy(result), "outcome_id")["outcome_id"])
        self.assertIs(result["production_eligible"], False)
        self.assertEqual(result["environment"], "shadow")
        value["source"]["source_id"] = "changed"
        self.assertEqual(result["source"]["source_id"], "fixture")

    def test_equivalent_input_timezones_normalize_identically(self):
        value = fixture()
        original = self.label(value, [5])
        value["evaluated_at"] = "2026-12-01T08:00:00+08:00"
        for bar in value["bars"]:
            bar["close_at"] = normalize_time(bar["close_at"])
            bar["available_at"] = normalize_time(bar["available_at"])
        self.assertEqual(original, self.label(value, [5]))

    def test_rebuilt_decision_rejects_tampering_even_if_rehashed(self):
        variants = [{"execution_allowed": 0}, {"execution_allowed": True}, {"schema_version": True},
                    {"confidence": 0.5}, {"fact_hash": "0" * 64}, {"environment": "paper"},
                    {"code": "600000"}, {"extra": True}, {"versions": {"data": "wrong"}},
                    {"decision_id": "0" * 64}]
        for changes in variants:
            value = fixture()
            value["decision"].update(changes)
            if "decision_id" not in changes:
                rehash(value["decision"], "decision_id")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.label(value)
        for field in fixture()["decision"]:
            value = fixture()
            del value["decision"][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.label(value)

    def test_invalid_pack_or_evaluated_at_is_rejected(self):
        value = fixture()
        value["pack"]["facts"][0]["data"]["price"] = 500
        with self.assertRaises(ValueError):
            self.label(value)
        for instant in ["2026-10-02T13:59:59+08:00", "2026-12-01", "bad", None, datetime.now(timezone.utc)]:
            value = fixture()
            value["evaluated_at"] = instant
            with self.subTest(instant=instant), self.assertRaises(ValueError):
                self.label(value)

    def test_horizons_strict_bounds_and_unique_values(self):
        for horizons in [[], [0], [-1], [251], [True], [1.0], [1, 1], list(range(1, 12)), (1,), "5"]:
            with self.subTest(horizons=horizons), self.assertRaises(ValueError):
                self.label(horizons=horizons)
        self.assertEqual([label["horizon"] for label in self.label(horizons=[20, 5])["labels"]], [20, 5])

    def test_calendar_strict_schema_dates_timezone_and_order(self):
        variants = [None, {}, {"version": "", "sessions": []}, dict(fixture()["calendar"], extra=True),
                    {"version": "v", "sessions": []}, {"version": "v", "sessions": {}},
                    {"version": "v", "sessions": fixture()["calendar"]["sessions"] * 201}]
        for change in [{"session": "20261002"}, {"session": "2026-10-03"}, {"close_at": "2026-10-02T15:00:00"},
                       {"session": "2026-02-30"}, {"extra": True}]:
            value = copy.deepcopy(fixture()["calendar"])
            value["sessions"][0].update(change)
            variants.append(value)
        for sessions in [list(reversed(fixture()["calendar"]["sessions"])), fixture()["calendar"]["sessions"][:1] * 2]:
            variants.append({"version": "v", "sessions": sessions})
        for calendar in variants:
            value = fixture()
            value["calendar"] = calendar
            with self.subTest(calendar=calendar), self.assertRaises(ValueError):
                self.label(value)

    def test_calendar_requires_increasing_instants_even_when_dates_increase(self):
        value = fixture(count=2)
        value["calendar"]["sessions"] = [{"session": "2026-10-02", "close_at": "2026-10-02T23:00:00-12:00"},
                                            {"session": "2026-10-03", "close_at": "2026-10-03T00:00:00+14:00"}]
        value["bars"] = []
        with self.assertRaises(ValueError):
            self.label(value)

    def test_bar_strict_schema_order_dates_prices_and_availability(self):
        variants = [None, {}, tuple(fixture()["bars"]), fixture()["bars"] * 201,
                    list(reversed(fixture()["bars"])), fixture()["bars"][:1] * 2]
        for changes in [{"session": "2026-10-03"}, {"close": True}, {"close": 0}, {"close": -1},
                        {"close": float("nan")}, {"close": float("inf")}, {"close": "1"},
                        {"close_at": "2026-10-02T15:01:00+08:00"}, {"available_at": "2026-10-02T14:59:00+08:00"},
                        {"available_at": "2026-10-02T15:00:00"}, {"extra": True}]:
            bars = copy.deepcopy(fixture()["bars"])
            bars[0].update(changes)
            variants.append(bars)
        for field in fixture()["bars"][0]:
            bars = copy.deepcopy(fixture()["bars"])
            del bars[0][field]
            variants.append(bars)
        for bars in variants:
            value = fixture()
            value["bars"] = bars
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                self.label(value)

    def test_empty_bars_yield_missing_bar(self):
        value = fixture()
        value["bars"] = []
        self.assertEqual(self.label(value, [5])["labels"][0]["reason"], "missing_bar")

    def test_source_strict_fields_and_declared_basis(self):
        for changes in [{"source_id": ""}, {"source_hash": "A" * 64}, {"source_hash": "x" * 64},
                        {"source_hash": "a" * 63}, {"price_basis": "qfq"}, {"availability_status": "verified"},
                        {"extra": True}]:
            value = fixture()
            value["source"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.label(value)
        for basis in ["raw", "qfq_snapshot", "hfq_snapshot"]:
            value = fixture()
            value["source"]["price_basis"] = basis
            self.assertEqual(self.label(value, [5])["source"]["price_basis"], basis)

    def test_non_json_and_cycles_rejected(self):
        value = fixture()
        value["source"]["source_id"] = {"tuple": (1, 2)}
        with self.assertRaises(ValueError):
            self.label(value)
        value = fixture()
        value["calendar"]["sessions"].append(value["calendar"])
        with self.assertRaises(ValueError):
            self.label(value)

    def test_return_overflow_is_value_error_not_nonfinite_output(self):
        for start, end in [(1e-308, 1e308), (1, 10 ** 400), (10 ** 400, 1)]:
            value = fixture()
            value["bars"][0]["close"] = start
            value["bars"][5]["close"] = end
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.label(value, [5])


if __name__ == "__main__":
    unittest.main()
