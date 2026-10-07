import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_operator.legacy import ingest_legacy
from ai_operator.memory import open_store


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.store = open_store(self.root / "memory.sqlite")
        self.addCleanup(self.store.close)

    def line(self, **changes):
        record = {"ts": "2026-10-03 18:38:50", "d": "2026-10-03", "sid": "9dim", "code": "600663.SH"}
        record.update(changes)
        return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")

    def write(self, data, filename="council_proposals.jsonl"):
        path = self.reports / filename
        path.write_bytes(data)
        return path

    def checkpoint(self, path):
        return self.store.get_checkpoint(os.path.normcase(str(path.resolve())))

    def event(self, payload, kind="council.proposal", environment="shadow", source="legacy/council_proposals.jsonl", available_at=None, occurred_at="2026-10-03T10:38:50+00:00"):
        source_id = str(payload["id"]) if "id" in payload else hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        event_id = self.store.append_event({"schema_version": 1, "kind": kind, "environment": environment, "source": source, "source_id": source_id, "occurred_at": occurred_at, "available_at": available_at, "payload": payload})
        return self.store.get_event(event_id)

    def test_import_preserves_payload_and_repeated_import_is_empty(self):
        raw = self.line(name="陆家嘴", lessons=["仅留存，不自动审核"])
        path = self.write(raw)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        first = ingest_legacy(self.store, self.reports)
        self.assertEqual((first["inserted"], first["errors"], len(first["missing"])), (1, 0, 4))
        stored = self.event(json.loads(raw))
        self.assertEqual(stored["available_at"], None)
        self.assertEqual(stored["occurred_at"], "2026-10-03T10:38:50.000000+00:00")
        self.assertEqual(stored["payload"], json.loads(raw))
        self.assertEqual(self.store.snapshot()["events"], 1)
        second = ingest_legacy(self.store, self.reports)
        self.assertEqual((second["inserted"], second["duplicates"], second["errors"]), (0, 0, 0))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_half_line_never_advances_until_newline(self):
        full, half = self.line(id="a"), self.line(id="b")[:-1]
        path = self.write(full + half)
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["pending_bytes"]), (1, len(half)))
        self.assertEqual(self.checkpoint(path)["offset"], len(full))
        self.assertEqual(ingest_legacy(self.store, self.reports)["pending_bytes"], len(half))
        with path.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)

    def test_bad_complete_lines_report_once_and_advance(self):
        raw = b"{broken}\n" + b"[]\n" + b'{"code":"X"}\n' + b"\xff\n" + self.line()
        path = self.write(raw)
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["errors"]), (1, 4))
        self.assertEqual(self.checkpoint(path)["offset"], len(raw))
        self.assertEqual(ingest_legacy(self.store, self.reports)["errors"], 0)
        self.assertEqual(self.store.snapshot()["import_errors"], 4)

    def test_error_offsets_are_bytes_and_error_details_are_auditable(self):
        good = self.line(name="中文", id="a")
        path = self.write(good + b"broken\r\n" + b"\xff\n")
        original = self.store.commit_import
        captured = []
        def capture(*args, **kwargs):
            captured.extend(args[3])
            return original(*args, **kwargs)
        with patch.object(self.store, "commit_import", side_effect=capture):
            result = ingest_legacy(self.store, self.reports)
        self.assertEqual(result["errors"], 2)
        self.assertEqual([error["offset"] for error in captured], [len(good), len(good) + 8])
        self.assertEqual([error["type"] for error in captured], ["invalid_json", "invalid_utf8"])
        for error in captured:
            self.assertEqual(error["source"], os.path.normcase(str(path.resolve())))
            self.assertTrue(error["message"])

    def test_timezone_and_missing_or_invalid_dates(self):
        date_record = {"d": "2026-10-04", "id": "date"}
        raw = (json.dumps(date_record) + "\n").encode()
        raw += self.line(id="zone", ts="2026-10-03T18:38:50+08:00")
        raw += b'{"id":"missing"}\n'
        raw += self.line(id="invalid", ts="bad")
        raw += self.line(id="invalid-available", available_at="2026-10-03 18:38:50")
        self.write(raw)
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["errors"]), (2, 3))
        event = self.event(date_record, occurred_at="2026-10-03T16:00:00+00:00")
        self.assertEqual(event["occurred_at"], "2026-10-03T16:00:00.000000+00:00")
        self.assertEqual(self.store.snapshot()["events"], 2)

    def test_same_payload_with_different_json_formatting_has_one_identity(self):
        payload = {"ts": "2026-10-03 18:38:50", "sid": "9dim"}
        first = (json.dumps(payload) + "\n").encode()
        second = b'{"sid":"9dim","ts":"2026-10-03 18:38:50"}\n'
        self.write(first + second)
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["duplicates"]), (1, 1))

    def test_reviewed_historical_lesson_still_has_no_available_time(self):
        raw = self.line(id="old", lessons=["复盘经验"])
        self.write(raw, "council_reviews.jsonl")
        ingest_legacy(self.store, self.reports)
        event = self.event(json.loads(raw), kind="council.review", source="legacy/council_reviews.jsonl")
        self.assertEqual(self.store.snapshot()["events"], 1)
        self.store.put_lesson(event["event_id"], "显式审核", [], status="reviewed", available_at="2026-10-04T00:00:00+00:00")
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-05T00:00:00+00:00"), [])

    def test_rotation_and_same_size_prefix_rewrite_rescan_idempotently(self):
        original = self.line(id="a") + self.line(id="b")
        path = self.write(original)
        ingest_legacy(self.store, self.reports)
        path.write_bytes(self.line(id="a"))
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["duplicates"]), (0, 1))
        self.assertEqual(self.checkpoint(path)["generation"], 1)
        path.write_bytes(self.line(id="c"))
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(self.checkpoint(path)["generation"], 2)

    def test_all_five_sources_and_date_fallback(self):
        for filename in ("council_proposals.jsonl", "council_watch.jsonl", "council_reviews.jsonl", "council_actions.jsonl", "council_sim_proposals.jsonl"):
            self.write(self.line(id=filename, lessons=["经验"]), filename)
        self.write(b'{"d":"2026-10-04","code":"X"}\n', "ignored.jsonl")
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], len(result["files"]), result["missing"]), (5, 5, []))
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["events_by_environment"], {"paper": 1, "shadow": 4})
        self.assertEqual(snapshot["lessons"], 0)
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-05T00:00:00+00:00"), [])
        self.write(b'{"d":"2026-10-04","code":"X"}\n', "council_watch.jsonl")
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)

    def test_explicit_id_revision_and_available_at_are_preserved(self):
        raw = self.line(id="proposal-1", available_at="2026-10-03T11:00:00+00:00")
        path = self.write(raw)
        ingest_legacy(self.store, self.reports)
        event = self.event(json.loads(raw), available_at="2026-10-03T11:00:00+00:00")
        self.assertEqual(event["source_id"], "proposal-1")
        self.assertEqual(self.store.snapshot()["events"], 1)
        path.write_bytes(self.line(id="proposal-1", code="X"))
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)
        self.assertEqual(self.store.snapshot()["events"], 2)

    def test_missing_directory_has_no_filesystem_side_effect(self):
        missing = self.root / "absent"
        with self.assertRaises((ValueError, NotADirectoryError)):
            ingest_legacy(self.store, missing)
        self.assertFalse(missing.exists())

    def test_transaction_failure_leaves_progress_and_events_unchanged(self):
        path = self.write(self.line(id="a") + b"broken\n")
        original = self.store.commit_import
        def invalid_commit(source_key, checkpoint, events, errors, expected_checkpoint=None):
            events.append({"payload": {}})
            return original(source_key, checkpoint, events, errors, expected_checkpoint)
        with patch.object(self.store, "commit_import", side_effect=invalid_commit):
            with self.assertRaises(ValueError):
                ingest_legacy(self.store, self.reports)
        self.assertIsNone(self.checkpoint(path))
        self.assertEqual(self.store.snapshot()["events"], 0)
        self.assertEqual(self.store.snapshot()["import_errors"], 0)
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)

    def test_stale_progress_conflict_fails_and_can_retry(self):
        path = self.write(self.line(id="a"))
        other = open_store(self.root / "memory.sqlite")
        self.addCleanup(other.close)
        original = self.store.commit_import
        invoked = False
        def conflicting_commit(*args, **kwargs):
            nonlocal invoked
            if not invoked:
                invoked = True
                ingest_legacy(other, self.reports)
            return original(*args, **kwargs)
        with patch.object(self.store, "commit_import", side_effect=conflicting_commit):
            with self.assertRaises(Exception) as error:
                ingest_legacy(self.store, self.reports)
        self.assertRegex(str(error.exception), "进度|checkpoint|冲突")
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 0)
        self.assertEqual(self.checkpoint(path)["offset"], path.stat().st_size)

    def test_append_during_commit_is_read_on_next_call(self):
        path = self.write(self.line(id="a"))
        original = self.store.commit_import
        appended = False
        def append_then_commit(*args, **kwargs):
            nonlocal appended
            if not appended:
                appended = True
                with path.open("ab") as stream:
                    stream.write(self.line(id="b"))
            return original(*args, **kwargs)
        with patch.object(self.store, "commit_import", side_effect=append_then_commit):
            self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)

    def test_append_during_read_respects_captured_file_length(self):
        path = self.write(self.line(id="a"))
        original = os.fstat
        appended = False
        def capture_then_append(fd):
            nonlocal appended
            snapshot = original(fd)
            if not appended:
                appended = True
                with path.open("ab") as stream:
                    stream.write(self.line(id="b"))
            return snapshot
        with patch("ai_operator.legacy.os.fstat", side_effect=capture_then_append):
            result = ingest_legacy(self.store, self.reports)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(ingest_legacy(self.store, self.reports)["inserted"], 1)

    def test_file_replacement_with_same_content_starts_new_generation(self):
        path = self.write(self.line(id="a"))
        ingest_legacy(self.store, self.reports)
        replacement = self.reports / "replacement"
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        result = ingest_legacy(self.store, self.reports)
        self.assertEqual((result["inserted"], result["duplicates"]), (0, 1))
        self.assertEqual(self.checkpoint(path)["generation"], 1)


if __name__ == "__main__":
    unittest.main()
