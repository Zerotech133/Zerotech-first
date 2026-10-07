import json
import math
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from ai_operator.contracts import canonical_json, normalize_time, validate_event
from ai_operator.memory import open_store


def event(**updates):
    value = {
        "schema_version": 1,
        "kind": "council.proposal",
        "environment": "shadow",
        "source": "legacy/council_proposals.jsonl",
        "source_id": "stable-key",
        "occurred_at": "2026-10-03T18:38:50+08:00",
        "available_at": "2026-10-03T11:00:00Z",
        "payload": {"code": "600663.SH", "strategy_id": "9dim"},
    }
    value.update(updates)
    return value


class ContractTests(unittest.TestCase):
    def test_canonical_json_rejects_non_json_values_and_nonfinite_numbers(self):
        self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')
        for value in ({"a": math.nan}, {"a": math.inf}, {1: "x"}, {"a": (1,)}, {"a": object()}):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                canonical_json(value)

    def test_times_require_zone_and_normalize_to_utc(self):
        self.assertEqual(normalize_time("2026-10-03T18:38:50+08:00"), "2026-10-03T10:38:50.000000+00:00")
        for value in (None, 123, "2026-10-03", "2026-10-03T10:00:00", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_time(value)

    def test_validation_defaults_and_rejects_forged_fields(self):
        value = event()
        del value["available_at"]
        del value["schema_version"]
        validated = validate_event(value)
        self.assertIsNone(validated["available_at"])
        self.assertEqual(validated["schema_version"], 1)
        for update in ({"environment": "production"}, {"payload": []}, {"recorded_at": "x"}, {"event_id": "x"}, {"payload_hash": "x"}, {"schema_version": True}, {"kind": ""}, {"available_at": "bad"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_event(event(**update))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "memory.db"
        self.store = open_store(self.db)
        self.addCleanup(self.store.close)

    def assert_database_rejected_without_changes(self, database):
        def state():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                return (
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    connection.execute("PRAGMA journal_mode").fetchone()[0],
                    connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name").fetchall(),
                )

        before_state = state()
        before_bytes = database.read_bytes()
        sidecars = {suffix: Path(str(database) + suffix).read_bytes()
                    for suffix in ("-wal", "-journal") if Path(str(database) + suffix).exists()}
        for readonly in (False, True):
            with self.subTest(readonly=readonly):
                with self.assertRaises(ValueError):
                    with open_store(database, readonly=readonly):
                        pass
                self.assertEqual(state(), before_state)
                self.assertEqual(database.read_bytes(), before_bytes)
                for suffix in ("-wal", "-journal"):
                    sidecar = Path(str(database) + suffix)
                    if suffix in sidecars:
                        self.assertEqual(sidecar.read_bytes(), sidecars[suffix])
                    else:
                        self.assertFalse(sidecar.exists())

    def test_foreign_version_zero_and_one_databases_are_never_modified(self):
        for version in (0, 1):
            with self.subTest(version=version):
                database = self.db.parent / f"foreign-{version}.db"
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("CREATE TABLE business (id INTEGER)")
                    connection.execute(f"PRAGMA user_version={version}")
                    connection.commit()
                self.assert_database_rejected_without_changes(database)

    def test_incompatible_same_name_database_is_never_modified(self):
        database = self.db.parent / "incompatible.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE events (event_id TEXT)")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        self.assert_database_rejected_without_changes(database)

    def test_foreign_uncheckpointed_wal_is_never_modified(self):
        script = (
            "import os, sqlite3, sys; "
            "connection=sqlite3.connect(sys.argv[1]); "
            "connection.execute('PRAGMA journal_mode=WAL'); "
            "connection.execute('CREATE TABLE business (id INTEGER)'); "
            "connection.execute('INSERT INTO business VALUES (1)'); "
            "connection.execute('PRAGMA user_version=' + sys.argv[2]); "
            "connection.commit(); os._exit(0)"
        )
        for version in (0, 1):
            with self.subTest(version=version):
                database = self.db.parent / f"foreign-wal-{version}.db"
                result = subprocess.run([sys.executable, "-B", "-c", script, str(database), str(version)],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(Path(str(database) + "-wal").exists())
                self.assert_database_rejected_without_changes(database)

    def test_version_one_requires_complete_tool_schema(self):
        changes = (
            "DROP TABLE checkpoints",
            "DROP INDEX event_recall",
            "ALTER TABLE events ADD COLUMN foreign_column TEXT",
            "CREATE TABLE business (id INTEGER)",
        )
        for number, statement in enumerate(changes):
            with self.subTest(statement=statement):
                database = self.db.parent / f"incomplete-{number}.db"
                with open_store(database) as store:
                    store.append_event(event())
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute(statement)
                    connection.commit()
                self.assert_database_rejected_without_changes(database)

    def test_existing_complete_tool_database_remains_readable_and_writable(self):
        event_id = self.store.append_event(event())
        self.store.close()
        with open_store(self.db, readonly=True) as store:
            self.assertEqual(store.get_event(event_id)["payload"]["code"], "600663.SH")
        with open_store(self.db) as store:
            store.append_event(event(source_id="second"))
            self.assertEqual(store.snapshot()["events"], 2)

    def test_event_deduplication_revision_and_detached_payload(self):
        value = event()
        first = self.store.append_event(value)
        value["payload"]["code"] = "changed"
        self.assertEqual(self.store.get_event(first)["payload"]["code"], "600663.SH")
        self.assertEqual(first, self.store.append_event(event(occurred_at="2026-10-03T10:38:50Z")))
        second = self.store.append_event(event(payload={"code": "revision"}))
        self.assertNotEqual(first, second)
        result = self.store.get_event(first)
        self.assertEqual(result["event_id"], first)
        self.assertEqual(len(result["payload_hash"]), 64)
        self.assertIsNotNone(result["recorded_at"])
        self.assertIsNone(self.store.get_event("missing"))
        self.assertEqual(self.store.snapshot()["events"], 2)

    def test_wal_timeout_and_context_manager(self):
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        with open_store(self.db) as second:
            self.assertGreaterEqual(second._conn.execute("PRAGMA busy_timeout").fetchone()[0], 1000)
            self.assertEqual(second.snapshot()["events"], 0)
        second.close()
        with self.assertRaises((ValueError, sqlite3.ProgrammingError)):
            second.snapshot()

    def test_import_commits_events_errors_and_checkpoint_atomically(self):
        error = {"source": "source", "offset": 10, "type": "bad_json", "message": "坏行"}
        result = self.store.commit_import("source", {"offset": 20}, [event(), event()], [error])
        self.assertEqual(result, {"inserted": 1, "duplicates": 1, "errors": 1})
        self.assertEqual(self.store.get_checkpoint("source"), {"offset": 20})
        self.assertEqual(self.store.snapshot()["import_errors"], 1)
        result = self.store.commit_import("source", {"offset": 30}, [event()], [], expected_checkpoint={"offset": 20})
        self.assertEqual(result["duplicates"], 1)

    def test_checkpoint_conflict_does_not_write(self):
        self.store.commit_import("source", {"offset": 20}, [event()], [])
        with self.assertRaises(ValueError):
            self.store.commit_import("source", {"offset": 30}, [event(source_id="other")], [{"message": "x"}])
        self.assertEqual(self.store.get_checkpoint("source"), {"offset": 20})
        self.assertEqual(self.store.snapshot()["events"], 1)
        self.assertEqual(self.store.snapshot()["import_errors"], 0)

    def test_import_bad_event_rolls_back_everything(self):
        with self.assertRaises(ValueError):
            self.store.commit_import("source", {"offset": 20}, [event(), event(payload=[])], [{"message": "x"}])
        self.assertIsNone(self.store.get_checkpoint("source"))
        self.assertEqual(self.store.snapshot()["events"], 0)

    def test_database_failure_rolls_back_import(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT, '中断'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.commit_import("source", {"offset": 20}, [event()], [{"message": "x"}])
        self.assertEqual(self.store.snapshot()["events"], 0)
        self.assertEqual(self.store.snapshot()["import_errors"], 0)

    def test_concurrent_independent_connections_deduplicate(self):
        barrier = threading.Barrier(3)
        failures = []
        results = []

        def write():
            try:
                with open_store(self.db) as store:
                    barrier.wait(timeout=5)
                    results.append(store.append_event(event()))
            except Exception as exc:
                failures.append(exc)

        workers = [threading.Thread(target=write) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(failures)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.store.snapshot()["events"], 1)

    def test_concurrent_import_checkpoint_compare_and_swap(self):
        barrier = threading.Barrier(3)
        outcomes = []

        def write(index):
            with open_store(self.db) as store:
                barrier.wait(timeout=5)
                try:
                    store.commit_import("source", {"offset": index}, [event(source_id=str(index))], [])
                    outcomes.append("committed")
                except ValueError:
                    outcomes.append("conflict")

        workers = [threading.Thread(target=write, args=(index,)) for index in (1, 2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=10)
        self.assertCountEqual(outcomes, ["committed", "conflict"])
        self.assertEqual(self.store.snapshot()["events"], 1)

    def test_lesson_idempotency_and_explicit_review(self):
        event_id = self.store.append_event(event())
        lesson = self.store.put_lesson(event_id, "观察后再决策", ["risk", "price"])
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-04T00:00:00Z"), [])
        reviewed = self.store.put_lesson(event_id, "观察后再决策", ["price", "risk"], status="reviewed", available_at="2026-10-03T11:00:00Z")
        self.assertEqual(lesson, reviewed)
        result = self.store.recall({"environment": "shadow", "code": "600663.SH", "strategy_id": "9dim", "tags": ["risk"]}, "2026-10-04T00:00:00Z")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lesson_id"], lesson)
        self.assertEqual(result[0]["event_id"], event_id)
        self.assertEqual(self.store.recall({"environment": "paper"}, "2026-10-04T00:00:00Z"), [])
        self.assertEqual(self.store.recall({"environment": "shadow", "tags": ["missing"]}, "2026-10-04T00:00:00Z"), [])
        self.store.put_lesson(event_id, "观察后再决策", ["risk", "price"], status="rejected")
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-04T00:00:00Z"), [])

    def test_lesson_future_availability_and_expiry(self):
        event_id = self.store.append_event(event())
        self.store.put_lesson(event_id, "later", [], status="reviewed", available_at="2026-10-04T00:00:00Z", expires_at="2026-10-05T00:00:00Z")
        query = {"environment": "shadow"}
        self.assertEqual(self.store.recall(query, "2026-10-03T23:59:59Z"), [])
        self.assertEqual(len(self.store.recall(query, "2026-10-04T00:00:00Z")), 1)
        self.assertEqual(self.store.recall(query, "2026-10-05T00:00:00Z"), [])
        self.assertEqual(self.store.snapshot(as_of="2026-10-03T23:59:59Z")["recallable_lessons"], 0)
        self.assertEqual(self.store.snapshot(as_of="2026-10-04T00:00:00Z")["recallable_lessons"], 1)

    def test_audit_only_event_cannot_be_made_recallable(self):
        event_id = self.store.append_event(event(available_at=None))
        self.store.put_lesson(event_id, "历史记忆", [], status="reviewed", available_at="2026-10-04T00:00:00Z")
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-05T00:00:00Z"), [])
        self.assertEqual(self.store.snapshot()["recallable_lessons"], 0)

    def test_invalid_lessons_and_queries_are_rejected(self):
        event_id = self.store.append_event(event())
        for kwargs in ({"status": "approved"}, {"available_at": "2026-10-03T10:00:00Z"}, {"expires_at": "2026-10-03T10:00:00Z"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.put_lesson(event_id, "x", [], **kwargs)
        for tags in ("risk", [1]):
            with self.assertRaises(ValueError):
                self.store.put_lesson(event_id, "x", tags)
        with self.assertRaises(ValueError):
            self.store.put_lesson("missing", "x", [])
        for query in ({}, {"environment": "production"}, {"environment": "shadow", "tags": "risk"}):
            with self.assertRaises(ValueError):
                self.store.recall(query, "2026-10-04T00:00:00Z")
        with self.assertRaises(ValueError):
            self.store.recall({"environment": "shadow"}, "2026-10-04", limit=5)
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-04T00:00:00Z", limit=0), [])

    def test_recall_is_deterministic_limited_and_supports_legacy_sid(self):
        for index in range(3):
            event_id = self.store.append_event(event(source_id=str(index), payload={"code": "x", "sid": "9dim"}))
            self.store.put_lesson(event_id, str(index), [], status="reviewed", available_at="2026-10-03T11:00:00Z")
        query = {"environment": "shadow", "strategy_id": "9dim"}
        first = self.store.recall(query, "2026-10-04T00:00:00Z", limit=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(first, self.store.recall(query, "2026-10-04T00:00:00Z", limit=2))

    def test_readonly_does_not_create_database_and_rejects_mutations(self):
        missing = Path(self.tmp.name) / "missing" / "memory.db"
        with self.assertRaises((ValueError, sqlite3.OperationalError, FileNotFoundError)):
            open_store(missing, readonly=True)
        self.assertFalse(missing.parent.exists())
        self.store.append_event(event())
        with open_store(self.db, readonly=True) as readonly:
            self.assertEqual(readonly.snapshot()["events"], 1)
            with self.assertRaises((ValueError, sqlite3.OperationalError)):
                readonly.append_event(event(source_id="new"))

    def test_readonly_does_not_initialize_empty_database(self):
        empty = Path(self.tmp.name) / "empty.db"
        empty.touch()
        before = empty.read_bytes()
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            open_store(empty, readonly=True)
        self.assertEqual(empty.read_bytes(), before)

    def test_readonly_observes_active_wal_without_modifying_database(self):
        self.store._conn.execute("PRAGMA wal_autocheckpoint=0")
        before = self.db.read_bytes()
        event_id = self.store.append_event(event())
        self.assertTrue(Path(str(self.db) + "-wal").is_file())
        wal_before = Path(str(self.db) + "-wal").read_bytes()
        with open_store(self.db, readonly=True) as readonly:
            self.assertIsNotNone(readonly.get_event(event_id))
            self.assertEqual(readonly.snapshot()["events"], 1)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(Path(str(self.db) + "-wal").read_bytes(), wal_before)

    def test_readonly_closed_wal_database_preserves_main_file(self):
        event_id = self.store.append_event(event())
        self.store.close()
        before = self.db.read_bytes()
        with open_store(self.db, readonly=True) as readonly:
            self.assertIsNotNone(readonly.get_event(event_id))
        self.assertEqual(self.db.read_bytes(), before)

    def test_new_lesson_default_availability_does_not_backdate_knowledge(self):
        event_id = self.store.append_event(event())
        with patch("ai_operator.memory.utc_now", return_value="2026-10-06T00:00:00.000000+00:00"):
            self.store.put_lesson(event_id, "新复盘", [], status="reviewed")
        self.assertEqual(self.store.recall({"environment": "shadow"}, "2026-10-05T00:00:00Z"), [])
        result = self.store.recall({"environment": "shadow"}, "2026-10-06T00:00:00Z")
        self.assertEqual(len(result), 1)

    def test_review_default_availability_and_repeat_preserve_expiry(self):
        event_id = self.store.append_event(event())
        with patch("ai_operator.memory.utc_now", return_value="2026-10-04T00:00:00.000000+00:00"):
            lesson_id = self.store.put_lesson(event_id, "待审核", [], expires_at="2026-10-08T00:00:00Z")
        with patch("ai_operator.memory.utc_now", return_value="2026-10-06T00:00:00.000000+00:00"):
            self.assertEqual(self.store.put_lesson(event_id, "待审核", [], status="reviewed"), lesson_id)
        with patch("ai_operator.memory.utc_now", return_value="2026-10-07T00:00:00.000000+00:00"):
            self.assertEqual(self.store.put_lesson(event_id, "待审核", [], status="reviewed"), lesson_id)
        query = {"environment": "shadow"}
        self.assertEqual(self.store.recall(query, "2026-10-05T00:00:00Z"), [])
        result = self.store.recall(query, "2026-10-06T00:00:00Z")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["expires_at"], "2026-10-08T00:00:00.000000+00:00")
        self.assertEqual(self.store.recall(query, "2026-10-08T00:00:00Z"), [])

    def test_export_complete_rebuildable_and_retry_has_no_duplicate_lines(self):
        ids = {self.store.append_event(event()), self.store.append_event(event(source_id="2"))}
        target = Path(self.tmp.name) / "export"
        self.assertEqual(self.store.snapshot()["pending_events"], 2)
        self.store.export_pending(target)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)
        self.store.export_pending(target)
        lines = (target / "events.jsonl").read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        self.assertEqual({row["event_id"] for row in rows}, ids)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row, self.store.get_event(row["event_id"]))
        self.store.append_event(event(source_id="3"))
        self.store.export_pending(target)
        self.assertEqual(len((target / "events.jsonl").read_text(encoding="utf-8").splitlines()), 3)

    def test_failed_atomic_replace_preserves_pending_and_existing_file(self):
        self.store.append_event(event())
        target = Path(self.tmp.name) / "export"
        target.mkdir()
        original = target / "events.jsonl"
        original.write_text("old\n", encoding="utf-8")
        with patch("ai_operator.memory.os.replace", side_effect=OSError("磁盘失败")):
            with self.assertRaises(OSError):
                self.store.export_pending(target)
        self.assertEqual(original.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        self.store.export_pending(target)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)
        self.assertEqual(len(original.read_text(encoding="utf-8").splitlines()), 1)

    def test_export_database_ack_failure_is_recoverable(self):
        self.store.append_event(event())
        target = Path(self.tmp.name) / "export"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("CREATE TRIGGER fail_ack BEFORE UPDATE ON outbox BEGIN SELECT RAISE(ABORT, '中断'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.export_pending(target)
        self.assertTrue((target / "events.jsonl").is_file())
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("DROP TRIGGER fail_ack")
        self.store.export_pending(target)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)
        self.assertEqual(len((target / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_concurrent_exports_cannot_replace_newer_snapshot_with_older(self):
        self.store.append_event(event())
        target = Path(self.tmp.name) / "export"
        entered = threading.Event()
        release = threading.Event()
        failures = []
        real_replace = __import__("os").replace

        def delayed_replace(src, dst):
            if threading.current_thread().name == "older":
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("测试超时")
            return real_replace(src, dst)

        def export_old():
            try:
                with open_store(self.db) as store:
                    store.export_pending(target)
            except Exception as exc:
                failures.append(exc)

        def export_new():
            try:
                with open_store(self.db) as store:
                    store.append_event(event(source_id="new"))
                    store.export_pending(target)
            except Exception as exc:
                failures.append(exc)

        with patch("ai_operator.memory.os.replace", side_effect=delayed_replace):
            older = threading.Thread(target=export_old, name="older")
            older.start()
            self.assertTrue(entered.wait(timeout=5))
            newer = threading.Thread(target=export_new, name="newer")
            newer.start()
            release.set()
            older.join(timeout=10)
            newer.join(timeout=10)
        self.assertFalse(failures)
        self.assertEqual(len((target / "events.jsonl").read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(self.store.snapshot()["pending_events"], 0)


if __name__ == "__main__":
    unittest.main()
