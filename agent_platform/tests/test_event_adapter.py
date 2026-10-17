# -*- coding: utf-8 -*-
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "agent_platform" / "adapters" / "event_adapter.py"
_spec = importlib.util.spec_from_file_location("event_adapter_under_test", MODULE_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_report = _module.build_report


class EventAdapterTests(unittest.TestCase):
    def test_missing_sources_are_reported_without_creation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            report = build_report(root)
            self.assertTrue(report["read_only"])
            self.assertEqual(report["counts"]["total"], 0)
            self.assertEqual(len(report["source_errors"]), 3)
            self.assertFalse((root / "portal" / "data" / "portal.db").exists())

    def test_reads_jsonl_and_preserves_raw(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            path = root / "portal" / "work" / "push_flow.jsonl"
            path.parent.mkdir(parents=True)
            source = {"kind": "published", "ts": "2026-01-01T00:00:00+08:00", "payload": {"x": 1}}
            path.write_text(json.dumps(source) + "\n", encoding="utf-8")
            report = build_report(root)
            event = next(item for item in report["events"] if item["source"] == "push_flow")
            self.assertEqual(event["ts"], "2025-12-31T16:00:00+00:00")
            self.assertTrue(event["provenance"]["derived_source_id"])
            self.assertEqual(event["payload"]["x"], 1)
            self.assertEqual(event["raw"]["kind"], "published")
            self.assertEqual(len(event["event_hash"]), 64)

    def test_reads_portal_events_read_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            db = root / "portal" / "data" / "portal.db"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, trace_id TEXT, source TEXT, kind TEXT, payload TEXT)")
                con.execute("INSERT INTO events VALUES (1, '2026-01-01 00:00:00', 'tr-1', 'paper', 'created', '{\"a\":2}')")
            report = build_report(root)
            event = report["events"][0]
            self.assertEqual(event["source"], "paper")
            self.assertEqual(event["provenance"]["adapter_source"], "portal_db.events")
            self.assertEqual(event["trace_id"], "tr-1")
            self.assertEqual(event["payload"], {"a": 2})
            con = sqlite3.connect(db)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
