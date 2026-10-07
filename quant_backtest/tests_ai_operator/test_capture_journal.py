"""验证持久接收的原子性、时间、身份及离线完整性边界。"""

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_operator.contracts import canonical_json, utc_now
from ai_operator.memory import open_store


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class CaptureJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.database = self.directory / "journal.sqlite"
        self.store = open_store(self.database)
        self.addCleanup(self.store.close)
        market = self.directory / "market.json"
        calendar = self.directory / "calendar.json"
        market.write_text(json.dumps({"bars": [
            {"session": "2026-01-05", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 2}
        ]}), encoding="utf-8")
        calendar.write_text(json.dumps({"rows": [
            {"exchange": "SSE", "cal_date": "20260105", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260106", "is_open": 1}
        ]}), encoding="utf-8")
        self.source = {"path": str(market), "source_id": "fixture-price", "code": "000001.SH",
                       "price_basis": "raw", "revision": "v1"}
        self.calendar = {"paths": [str(calendar)], "exchange": "SSE", "source_id": "fixture-calendar",
                         "revision": "v1", "coverage_start": "2026-01-05", "coverage_end": "2026-01-06"}

    def capture(self, previous=None, source=None, calendar=None):
        from ai_operator.capture_journal import capture_to_store
        return capture_to_store(self.store, self.source if source is None else source,
                                self.calendar if calendar is None else calendar, previous)

    @staticmethod
    def rehash(receipt):
        event = receipt["event"]
        payload = event["payload"]
        payload["snapshot"] = copy.deepcopy(receipt["snapshot"])
        payload["calendar_capture"] = copy.deepcopy(receipt["calendar_capture"])
        payload["stream_id"] = receipt["stream_id"]
        payload["previous_event_id"] = receipt["previous_event_id"]
        event["payload_hash"] = digest(payload)
        base = {key: value for key, value in event.items()
                if key not in {"event_id", "payload_hash", "recorded_at"}}
        event["event_id"] = digest(base)
        receipt["capture_event_id"] = event["event_id"]

    def test_capture_single_event_reopen_and_export(self):
        from ai_operator.capture_journal import load_capture, validate_receipt
        before = copy.deepcopy((self.source, self.calendar))
        receipt = self.capture()
        self.assertEqual(set(receipt), {"schema_version", "capture_event_id", "stream_id", "previous_event_id",
                                       "recorded_available_at", "snapshot", "calendar_capture", "event", "production_eligible"})
        self.assertIs(receipt["production_eligible"], False)
        self.assertEqual(receipt["event"]["kind"], "source.capture")
        self.assertEqual(receipt["event"]["payload"]["snapshot"], receipt["snapshot"])
        self.assertEqual(receipt["event"]["payload"]["calendar_capture"], receipt["calendar_capture"])
        self.assertEqual(self.store.snapshot()["events"], 1)
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        self.assertEqual((self.source, self.calendar), before)
        self.store.close()
        with open_store(self.database, readonly=True) as reader:
            self.assertEqual(load_capture(reader, receipt["capture_event_id"], utc_now()), receipt)
        with open_store(self.database) as writer:
            exported = writer.export_pending(str(self.directory / "outbox"))
        event = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
        self.assertEqual(event, receipt["event"])
        validated = validate_receipt(receipt)
        validated["snapshot"]["bars"].clear()
        self.assertEqual(len(receipt["snapshot"]["bars"]), 1)

    def test_stream_binds_frozen_identity(self):
        receipt = self.capture()
        self.assertEqual(receipt["stream_id"], digest({"source_id": "fixture-price", "code": "000001.SH",
            "period": "1d", "price_basis": "raw", "exchange": "SSE", "calendar_source_id": "fixture-calendar"}))

    def test_caller_cannot_supply_times_snapshots_or_unknown_fields(self):
        for value in ("received_at", "available_at", "snapshot", "calendar_capture", "observation_id", "unknown"):
            for source_side in (True, False):
                source, calendar = copy.deepcopy((self.source, self.calendar))
                (source if source_side else calendar)[value] = "2020-01-01T00:00:00Z"
                with self.subTest(value=value, source_side=source_side), self.assertRaises(ValueError):
                    self.capture(source=source, calendar=calendar)
        self.assertEqual(self.store.snapshot()["events"], 0)

    def test_strict_requests_reject_non_json_and_missing_fields(self):
        for source in (None, [], {}, self.source | {"path": Path(self.source["path"])},
                       self.source | {"format": float("inf")}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.capture(source=source if source is not None else [])
        for previous in ("", "bogus", True, 1):
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                self.capture(previous)
        self.assertEqual(self.store.snapshot()["events"], 0)

    def test_readonly_rejects_before_source_read(self):
        from ai_operator.capture_journal import capture_to_store
        with open_store(self.database, readonly=True) as reader:
            with patch("ai_operator.capture_journal.capture_snapshot", side_effect=AssertionError("不应读取")):
                with self.assertRaises(ValueError):
                    capture_to_store(reader, self.source, self.calendar)

    def test_calendar_failure_and_alignment_leave_no_half_event(self):
        Path(self.calendar["paths"][0]).write_text('{"rows":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(self.store.snapshot()["events"], 0)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)

    def test_closed_market_day_fails_alignment_before_append(self):
        Path(self.calendar["paths"][0]).write_text(json.dumps({"rows": [
            {"exchange": "SSE", "cal_date": "20260105", "is_open": 0},
            {"exchange": "SSE", "cal_date": "20260106", "is_open": 1}
        ]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(self.store.snapshot()["events"], 0)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)

    def test_existing_readonly_connection_observes_complete_commit(self):
        from ai_operator.capture_journal import load_capture
        with open_store(self.database, readonly=True) as reader:
            self.assertEqual(reader.snapshot()["events"], 0)
            receipt = self.capture()
            self.assertEqual(load_capture(reader, receipt["capture_event_id"], utc_now()), receipt)
            self.assertEqual(reader.snapshot()["events"], 1)
            self.assertEqual(reader.snapshot()["pending_events"], 1)

    def test_transaction_failure_rolls_back_event_and_outbox(self):
        self.store._conn.execute("CREATE TEMP TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.capture()
        self.assertEqual(self.store.snapshot()["events"], 0)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)

    def test_chain_revisions_history_limits_and_independent_observations(self):
        from ai_operator.capture_journal import capture_history
        first = self.capture()
        second = self.capture(first["capture_event_id"], source=self.source | {"revision": "v2"})
        third = self.capture(second["capture_event_id"])
        self.assertEqual(first["stream_id"], second["stream_id"])
        self.assertNotEqual(first["capture_event_id"], third["capture_event_id"])
        self.assertEqual([item["capture_event_id"] for item in capture_history(self.store, third["capture_event_id"], utc_now())],
                         [first["capture_event_id"], second["capture_event_id"], third["capture_event_id"]])
        self.assertEqual([item["capture_event_id"] for item in capture_history(self.store, third["capture_event_id"], utc_now(), 2)],
                         [second["capture_event_id"], third["capture_event_id"]])
        for limit in (True, 0, -1, 101, "2", 2.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                capture_history(self.store, third["capture_event_id"], utc_now(), limit)

    def test_fixed_clock_independent_captures_each_insert_one_observation(self):
        from ai_operator.capture_journal import load_capture
        instant = utc_now()
        with patch("ai_operator.contracts.utc_now", return_value=instant), \
                patch("ai_operator.memory.utc_now", return_value=instant):
            first = self.capture()
            second = self.capture()
            self.assertEqual(self.store.snapshot()["events"], 2)
            self.assertEqual(self.store.snapshot()["pending_events"], 2)
            self.assertNotEqual(first["capture_event_id"], second["capture_event_id"])
            self.assertEqual(first["snapshot"], second["snapshot"])
            self.assertEqual(first["calendar_capture"], second["calendar_capture"])
            self.assertEqual(first["stream_id"], second["stream_id"])
            self.assertEqual(first["recorded_available_at"], second["recorded_available_at"])
            for receipt in (first, second):
                self.assertRegex(receipt["event"]["payload"]["observation_id"], r"^[0-9a-f]{32}$")
                self.assertEqual(load_capture(self.store, receipt["capture_event_id"], instant), receipt)
            self.assertNotEqual(first["event"]["payload"]["observation_id"], second["event"]["payload"]["observation_id"])

    def test_fixed_clock_explicit_predecessor_allows_equal_timestamps(self):
        from ai_operator.capture_journal import capture_history
        instant = utc_now()
        with patch("ai_operator.contracts.utc_now", return_value=instant), \
                patch("ai_operator.memory.utc_now", return_value=instant):
            first = self.capture()
            second = self.capture(first["capture_event_id"])
            self.assertEqual(capture_history(self.store, second["capture_event_id"], instant), [first, second])
            self.assertEqual(first["recorded_available_at"], second["recorded_available_at"])
            self.assertEqual(self.store.snapshot()["events"], 2)
            self.assertRegex(str(second["event"]["payload"].get("observation_id")), r"^[0-9a-f]{32}$")

    def test_receipt_requires_observation_id_even_after_rehashing(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        receipt["event"]["payload"].pop("observation_id", None)
        self.rehash(receipt)
        with self.assertRaises(ValueError):
            validate_receipt(receipt)

    def test_receipt_rejects_invalid_observation_id_even_after_rehashing(self):
        from ai_operator.capture_journal import validate_receipt
        original = self.capture()
        for value in (None, True, 1, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32,
                      "00000000-0000-0000-0000-000000000000"):
            receipt = copy.deepcopy(original)
            receipt["event"]["payload"]["observation_id"] = value
            self.rehash(receipt)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_receipt(receipt)

    def test_wrong_predecessor_identity_and_missing_rejected_without_write(self):
        first = self.capture()
        for source in (self.source | {"code": "000002.SH"}, self.source | {"price_basis": "qfq_snapshot"},
                       self.source | {"source_id": "other"}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.capture(first["capture_event_id"], source=source)
        with self.assertRaises(ValueError):
            self.capture(first["capture_event_id"], calendar=self.calendar | {"source_id": "other"})
        with self.assertRaises(ValueError):
            self.capture("f" * 64)
        self.assertEqual(self.store.snapshot()["events"], 1)

    def test_load_durable_time_not_received_time(self):
        from ai_operator.capture_journal import load_capture
        from datetime import datetime, timedelta
        current = datetime.fromisoformat(utc_now())
        clock = [(current - timedelta(seconds=2)).isoformat(timespec="microseconds")]
        recorded = (current - timedelta(seconds=1)).isoformat(timespec="microseconds")

        def record_time():
            clock[0] = recorded
            return recorded

        with patch("ai_operator.contracts.utc_now", side_effect=lambda: clock[0]), \
                patch("ai_operator.memory.utc_now", side_effect=record_time):
            receipt = self.capture()
        received = receipt["event"]["occurred_at"]
        self.assertGreater(receipt["recorded_available_at"], received)
        with self.assertRaises(ValueError):
            load_capture(self.store, receipt["capture_event_id"], received)
        self.assertEqual(load_capture(self.store, receipt["capture_event_id"], receipt["recorded_available_at"]), receipt)
        future = (datetime.fromisoformat(utc_now()) + timedelta(days=1)).isoformat()
        self.assertEqual(load_capture(self.store, receipt["capture_event_id"], future), receipt)
        for event_id, as_of in (("f" * 64, utc_now()), (True, utc_now()), (receipt["capture_event_id"], "2026-01-01")):
            with self.subTest(event_id=event_id, as_of=as_of), self.assertRaises(ValueError):
                load_capture(self.store, event_id, as_of)

    def test_receipt_tamper_hash_and_rehashed_semantics_rejected(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        for mutation in (
            lambda r: r.update(schema_version=True),
            lambda r: r.update(production_eligible=True),
            lambda r: r.update(recorded_available_at="2020-01-01T00:00:00Z"),
            lambda r: r["event"].update(source="other"),
            lambda r: r["event"].update(environment="paper"),
            lambda r: r["event"].update(kind="source.review"),
            lambda r: r["event"].update(available_at="2020-01-01T00:00:00Z"),
            lambda r: r["event"].update(recorded_at="2020-01-01T00:00:00Z"),
            lambda r: r.update(stream_id="f" * 64),
            lambda r: r["snapshot"]["bars"][0].update(close=999),
        ):
            tampered = copy.deepcopy(receipt)
            mutation(tampered)
            with self.assertRaises(ValueError):
                validate_receipt(tampered)
            self.rehash(tampered)
            with self.assertRaises(ValueError):
                validate_receipt(tampered)

    def test_receipt_strict_json_event_fields_and_self_predecessor(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        for mutation in (
            lambda r: r.update(extra=1), lambda r: r["event"].update(extra=1),
            lambda r: r["event"]["payload"].update(extra=1),
            lambda r: r["event"]["payload"].update(schema_version=True),
            lambda r: r.update(previous_event_id=True),
            lambda r: r["event"].update(recorded_at="2099-01-01T00:00:00Z"),
            lambda r: r.update(schema_version=float("nan")),
        ):
            tampered = copy.deepcopy(receipt)
            mutation(tampered)
            with self.assertRaises(ValueError):
                validate_receipt(tampered)
        tampered = copy.deepcopy(receipt)
        tampered["previous_event_id"] = receipt["capture_event_id"]
        tampered["event"]["payload"]["previous_event_id"] = receipt["capture_event_id"]
        with self.assertRaises(ValueError):
            validate_receipt(tampered)

    def test_load_rejects_non_capture_and_database_corruption(self):
        from ai_operator.capture_journal import load_capture
        event_id = self.store.append_event({"schema_version": 1, "kind": "other", "environment": "shadow",
            "source": "fixture", "source_id": "fixture", "occurred_at": utc_now(), "available_at": utc_now(), "payload": {}})
        with self.assertRaises(ValueError):
            load_capture(self.store, event_id, utc_now())
        receipt = self.capture()
        self.store._conn.execute("UPDATE events SET payload_hash=? WHERE event_id=?", ("f" * 64, receipt["capture_event_id"]))
        with self.assertRaises(ValueError):
            load_capture(self.store, receipt["capture_event_id"], utc_now())

    def test_receipt_outer_snapshot_type_substitution_is_rejected(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        for name in ("snapshot", "calendar_capture"):
            tampered = copy.deepcopy(receipt)
            tampered[name]["schema_version"] = True
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_receipt(tampered)

    def test_invalid_ohlc_rejected_even_after_rehashing_all_layers(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        receipt["snapshot"]["bars"][0]["close"] = 999
        receipt["snapshot"]["snapshot_id"] = digest({key: value for key, value in receipt["snapshot"].items()
                                                      if key != "snapshot_id"})
        self.rehash(receipt)
        with self.assertRaises(ValueError):
            validate_receipt(receipt)

    def test_reconstructed_valid_receipt_is_integrity_not_authenticity(self):
        from ai_operator.capture_journal import validate_receipt
        receipt = self.capture()
        receipt["snapshot"]["revision"] = "manually-reconstructed"
        receipt["snapshot"]["snapshot_id"] = digest({key: value for key, value in receipt["snapshot"].items()
                                                      if key != "snapshot_id"})
        self.rehash(receipt)
        result = validate_receipt(receipt)
        self.assertEqual(result["snapshot"]["revision"], "manually-reconstructed")
        self.assertIs(result["production_eligible"], False)
        self.assertEqual(result["snapshot"]["supplier_verification"], "unverified")

    def test_recording_clock_rollback_leaves_one_atomic_but_unreadable_event(self):
        from ai_operator.capture_journal import load_capture, validate_receipt
        with patch("ai_operator.memory.utc_now", return_value="2020-01-01T00:00:00.000000+00:00"):
            with self.assertRaises(ValueError):
                self.capture()
        self.assertEqual(self.store.snapshot()["events"], 1)
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        event_id = self.store._conn.execute("SELECT event_id FROM events").fetchone()[0]
        event = self.store.get_event(event_id)
        payload = event["payload"]
        receipt = {"schema_version": 1, "capture_event_id": event_id, "stream_id": payload["stream_id"],
            "previous_event_id": None, "snapshot": payload["snapshot"], "calendar_capture": payload["calendar_capture"],
            "event": event, "recorded_available_at": event["occurred_at"], "production_eligible": False}
        with self.assertRaises(ValueError):
            load_capture(self.store, event_id, utc_now())
        with self.assertRaises(ValueError):
            validate_receipt(receipt)

    def test_predecessor_clock_rollback_is_rejected_before_append(self):
        from ai_operator.provenance import capture_snapshot
        from ai_operator.trading_calendar import capture_calendar
        first = self.capture()
        with patch("ai_operator.contracts.utc_now", return_value="2026-01-07T00:00:00.000000+00:00"):
            snapshot = capture_snapshot(**self.source)
            calendar = capture_calendar(**self.calendar)
        with patch("ai_operator.capture_journal.capture_snapshot", return_value=snapshot), \
                patch("ai_operator.capture_journal.capture_calendar", return_value=calendar):
            with self.assertRaises(ValueError):
                self.capture(first["capture_event_id"])
        self.assertEqual(self.store.snapshot()["events"], 1)

    def test_history_rechecks_rehashed_wrong_stream_missing_and_time_order(self):
        from ai_operator.capture_journal import capture_history, load_capture
        first = self.capture()
        second = self.capture(first["capture_event_id"])
        other = self.capture(source=self.source | {"code": "000002.SH"})
        for previous in (other["capture_event_id"], "f" * 64):
            base = {name: copy.deepcopy(second["event"][name]) for name in
                    ("schema_version", "kind", "environment", "source", "source_id", "occurred_at", "available_at", "payload")}
            base["payload"]["previous_event_id"] = previous
            event_id = self.store.append_event(base)
            self.assertEqual(load_capture(self.store, event_id, utc_now())["previous_event_id"], previous)
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                capture_history(self.store, event_id, utc_now())
        base = {name: copy.deepcopy(second["event"][name]) for name in
                ("schema_version", "kind", "environment", "source", "source_id", "occurred_at", "available_at", "payload")}
        early = first["snapshot"]["received_at"]
        snapshot = base["payload"]["snapshot"]
        snapshot["received_at"] = early
        for bar in snapshot["bars"]:
            bar["available_at"] = early
        snapshot["snapshot_id"] = digest({k: v for k, v in snapshot.items() if k != "snapshot_id"})
        calendar = base["payload"]["calendar_capture"]
        calendar["received_at"] = early
        calendar["capture_id"] = digest({k: v for k, v in calendar.items() if k != "capture_id"})
        base["occurred_at"] = base["available_at"] = early
        event_id = self.store.append_event(base)
        load_capture(self.store, event_id, utc_now())
        with self.assertRaises(ValueError):
            capture_history(self.store, event_id, utc_now())

    def test_offline_receipt_does_not_require_source_files(self):
        from ai_operator.capture_journal import load_capture, validate_receipt
        receipt = self.capture()
        Path(self.source["path"]).unlink()
        Path(self.calendar["paths"][0]).unlink()
        self.assertEqual(validate_receipt(receipt), receipt)
        self.assertEqual(load_capture(self.store, receipt["capture_event_id"], utc_now()), receipt)


if __name__ == "__main__":
    unittest.main()
