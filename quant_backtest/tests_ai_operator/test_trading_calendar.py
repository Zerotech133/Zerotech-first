"""交易日历的实际读取、逐日覆盖和冻结完整性契约。"""

import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from ai_operator.contracts import canonical_json


def rehash(value):
    value["capture_id"] = hashlib.sha256(canonical_json(
        {key: item for key, item in value.items() if key != "capture_id"}).encode("utf-8")).hexdigest()
    return value


class TradingCalendarTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.trading_calendar"), "交易日历模块尚未实现")
        self.calendar = importlib.import_module("ai_operator.trading_calendar")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [{"exchange": "SSE", "cal_date": "2026-10-01", "is_open": 0},
                     {"exchange": "SSE", "cal_date": "20261002", "is_open": 0},
                     {"exchange": "SSE", "cal_date": "2026-10-03", "is_open": 1}]

    def write(self, name="calendar.json", rows=None, raw=None):
        path = self.root / name
        path.write_bytes(raw if raw is not None else json.dumps(
            {"rows": self.rows if rows is None else rows}).encode("utf-8"))
        return path

    def capture(self, paths=None, **changes):
        values = dict(exchange="SSE", source_id="fixture", revision="r1",
                      coverage_start="2026-10-01", coverage_end="2026-10-03")
        values.update(changes)
        return self.calendar.capture_calendar([self.write()] if paths is None else paths, **values)

    def test_explicit_holiday_calendar_keeps_local_close_and_exact_bytes(self):
        path = self.write()
        data = path.read_bytes()
        before = datetime.now(timezone.utc)
        result = self.capture([path])
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, datetime.fromisoformat(result["received_at"]))
        self.assertLessEqual(datetime.fromisoformat(result["received_at"]), after)
        self.assertEqual(result["calendar"]["sessions"], [
            {"session": "2026-10-03", "close_at": "2026-10-03T15:00:00+08:00"}])
        self.assertEqual(result["days"], [{"session": "2026-10-01", "is_open": 0},
                                           {"session": "2026-10-02", "is_open": 0},
                                           {"session": "2026-10-03", "is_open": 1}])
        self.assertEqual(result["source_files"], [{"path": str(path.resolve()),
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "format": "json"}])
        self.assertIs(result["production_eligible"], False)
        self.assertEqual(result["supplier_verification"], "unverified")
        self.assertEqual(result["capture_id"], rehash(copy.deepcopy(result))["capture_id"])

    def test_receive_time_is_after_file_read_and_cannot_be_injected(self):
        path = self.write()
        def actual_now():
            path.write_bytes(b"changed after read")
            return "2026-10-07T08:00:00.000000+00:00"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch("ai_operator.contracts.utc_now", side_effect=actual_now):
            result = self.capture([path])
        self.assertEqual(result["source_files"][0]["sha256"], digest)
        self.assertEqual(result["received_at"], "2026-10-07T08:00:00.000000+00:00")
        with self.assertRaises(TypeError):
            self.capture(received_at="1991-01-01T00:00:00+00:00")

    def test_closed_interval_is_valid_including_spring_festival(self):
        rows = [{"exchange": "SZSE", "cal_date": day, "is_open": 0}
                for day in ["2026-02-16", "2026-02-17", "2026-02-18"]]
        result = self.capture([self.write(rows=rows)], exchange="SZSE",
                              coverage_start="2026-02-16", coverage_end="2026-02-18")
        self.assertEqual(result["calendar"]["sessions"], [])
        self.assertEqual(self.calendar.validate_calendar_capture(result), result)

    def test_every_natural_day_required_and_empty_selection_rejected(self):
        for rows in [[], self.rows[1:], self.rows[:1] + self.rows[2:]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.capture([self.write(rows=rows)])

    def test_exchange_is_explicit_and_unrelated_rows_are_not_used(self):
        rows = self.rows + [{"exchange": "SZSE", "cal_date": "2026-10-03", "is_open": 0}]
        result = self.capture([self.write(rows=rows)])
        self.assertEqual(result["calendar"]["sessions"][0]["session"], "2026-10-03")
        for exchange in ["", "SH", "sse", None, True]:
            with self.subTest(exchange=exchange), self.assertRaises(ValueError):
                self.capture(exchange=exchange)

    def test_missing_security_bar_cannot_turn_open_day_into_closed_day(self):
        rows = [{"exchange": "SSE", "cal_date": "2026-10-01", "is_open": 1}]
        result = self.capture([self.write(rows=rows)], coverage_end="2026-10-01")
        self.assertEqual(len(result["calendar"]["sessions"]), 1)

    def test_identical_duplicates_merge_and_semantic_version_ignores_file_order(self):
        first = self.write("z.json", self.rows[::-1] + self.rows)
        second = self.write("a.json", self.rows)
        base = self.capture([first])
        multiple = self.capture([first, second])
        reverse = self.capture([second, first])
        self.assertEqual(base["calendar"]["version"], multiple["calendar"]["version"])
        self.assertEqual(multiple["calendar"], reverse["calendar"])
        self.assertEqual([item["path"] for item in multiple["source_files"]],
                         [str(second.resolve()), str(first.resolve())])
        self.assertNotEqual(base["capture_id"], multiple["capture_id"])

    def test_conflicting_duplicates_rejected_across_files(self):
        rows = copy.deepcopy(self.rows)
        rows[2]["is_open"] = 0
        with self.assertRaises(ValueError):
            self.capture([self.write("a.json"), self.write("b.json", rows)])

    def test_json_is_strict_for_duplicates_unknown_fields_and_invalid_numbers(self):
        invalid = [b'', b'{"rows":[],"rows":[]}', b'{"rows":[],"extra":1}',
                   b'{"rows":NaN}', b'{"rows":Infinity}', b'[]', b'\xff']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.capture([self.write(raw=raw)])
        for changes in [{"is_open": True}, {"is_open": 1.0}, {"is_open": "1"},
                        {"is_open": 2}, {"cal_date": "2026-1-01"}, {"cal_date": 20261001},
                        {"cal_date": "20260230"}, {"available_at": "old"}]:
            rows = copy.deepcopy(self.rows)
            rows[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.capture([self.write(rows=rows)])

    def test_limits_and_source_parameters(self):
        for changes in [{"source_id": " "}, {"revision": ""}, {"format": "csv"},
                        {"coverage_start": "2026-10-04"}, {"coverage_start": "2026-1-01"},
                        {"coverage_start": "1991-01-01"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.capture(**changes)
        path = self.write()
        for paths in [[], [path, path], [path, path.parent / "." / path.name], str(path),
                      [self.root], [self.root / "missing"], [path] * 33]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.capture(paths)
        large = self.root / "large.json"
        with large.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self.capture([large])

    def test_symlink_source_rejected(self):
        path = self.write()
        link = self.root / "link.json"
        try:
            link.symlink_to(path)
        except OSError as exc:
            self.skipTest(f"当前环境不能建立符号链接: {exc}")
        with self.assertRaises(ValueError):
            self.capture([link])

    def test_reparse_file_and_parent_rejected_before_read(self):
        path = self.write()
        original = Path.lstat
        for target in [path, path.parent]:
            def marked(item):
                info = original(item)
                if item == target:
                    return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                        st_dev=info.st_dev, st_ino=info.st_ino, st_file_attributes=0x400)
                return info
            with self.subTest(target=target), patch.object(Path, "lstat", marked), self.assertRaises(ValueError):
                self.capture([path])

    @unittest.skipUnless(os.name == "nt", "仅 Windows 提供真实 junction")
    def test_actual_parent_junction_rejected_without_changing_target(self):
        import _winapi
        path = self.write()
        original = path.read_bytes()
        junction = self.root / "alias"
        _winapi.CreateJunction(str(self.root), str(junction))
        try:
            with self.assertRaises(ValueError):
                self.capture([junction / path.name])
            self.assertEqual(path.read_bytes(), original)
        finally:
            junction.rmdir()

    def test_exact_32_mib_json_is_accepted(self):
        path = self.write()
        data = path.read_bytes()
        with path.open("ab") as stream:
            stream.write(b" " * (32 * 1024 * 1024 - len(data)))
        result = self.capture([path])
        self.assertEqual(result["source_files"][0]["size_bytes"], 32 * 1024 * 1024)

    def test_unc_and_windows_device_namespaces_rejected(self):
        prefix = chr(92) * 2
        for name in [prefix + "server/share/calendar.json", prefix + "?/UNC/server/share/calendar.json",
                     prefix + "./C:/calendar.json", "//server/share/calendar.json"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.capture([name])

    def test_source_replaced_after_read_keeps_original_parse_and_hash(self):
        path = self.write()
        original = self.calendar._read_file
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        def replace_after_read(item):
            data = original(item)
            item.write_bytes(b'{"rows":[]}')
            return data
        with patch.object(self.calendar, "_read_file", side_effect=replace_after_read):
            result = self.capture([path])
        self.assertEqual(len(result["days"]), 3)
        self.assertEqual(result["source_files"][0]["sha256"], digest)

    def test_calendar_capture_does_not_open_network_connections(self):
        with patch("socket.create_connection", side_effect=AssertionError("不允许联网")), \
                patch("socket.socket.connect", side_effect=AssertionError("不允许联网")):
            result = self.capture()
        self.assertEqual(len(result["days"]), 3)

    def test_unencodable_metadata_rejected_as_value_error(self):
        with self.assertRaises(ValueError):
            self.capture(source_id="\ud800")

    def test_validator_returns_deep_copy_without_reading_source(self):
        result = self.capture()
        original = copy.deepcopy(result)
        (self.root / "calendar.json").unlink()
        validated = self.calendar.validate_calendar_capture(result)
        self.assertEqual(result, original)
        self.assertEqual(validated, result)
        validated["days"][0]["is_open"] = 1
        self.assertEqual(result, original)

    def test_validator_rejects_raw_tampering_and_rehashed_invalid_semantics(self):
        result = self.capture()
        value = copy.deepcopy(result)
        value["revision"] = "changed"
        with self.assertRaises(ValueError):
            self.calendar.validate_calendar_capture(value)
        for changes in [{"schema_version": True}, {"production_eligible": 0},
                        {"production_eligible": True}, {"supplier_verification": "trusted"},
                        {"received_at": "2026-10-07T15:00:00"},
                        {"received_at": "9999-01-01T00:00:00.000000+00:00"},
                        {"exchange": "SH"}, {"days": []}, {"source_files": []},
                        {"extra": True}, {"source_id": ""}]:
            value = copy.deepcopy(result)
            value.update(changes)
            rehash(value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.calendar.validate_calendar_capture(value)
        for target, changes in [("days", {"is_open": True}),
                                ("days", {"session": "2026-10-02"}),
                                ("source_files", {"size_bytes": True}),
                                ("source_files", {"sha256": "invalid"}),
                                ("source_files", {"path": "relative.json"}),
                                ("source_files", {"format": "csv"})]:
            value = copy.deepcopy(result)
            value[target][0].update(changes)
            rehash(value)
            with self.subTest(target=target, changes=changes), self.assertRaises(ValueError):
                self.calendar.validate_calendar_capture(value)

    def test_calendar_version_and_session_mapping_are_revalidated(self):
        result = self.capture()
        for change in ["version", "closed", "time", "missing", "extra", "utc"]:
            value = copy.deepcopy(result)
            if change == "version":
                value["calendar"]["version"] = "0" * 64
            elif change == "missing":
                value["calendar"]["sessions"] = []
            elif change == "extra":
                value["calendar"]["sessions"][0]["extra"] = 1
            elif change == "closed":
                value["calendar"]["sessions"][0]["session"] = "2026-10-01"
            else:
                value["calendar"]["sessions"][0]["close_at"] = (
                    "2026-10-03T07:00:00+00:00" if change == "utc" else "2026-10-03T14:00:00+08:00")
            rehash(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.calendar.validate_calendar_capture(value)

    def test_non_json_values_rejected_even_if_python_equality_matches(self):
        result = self.capture()
        for field, replacement in [("days", tuple(result["days"])),
                                   ("schema_version", 1.0),
                                   ("production_eligible", 0.0)]:
            value = copy.deepcopy(result)
            value[field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.calendar.validate_calendar_capture(value)

    def test_parquet_reads_actual_tushare_fields_and_ignores_pretrade_date(self):
        import pandas as pd
        path = self.root / "calendar.parquet"
        rows = copy.deepcopy(self.rows)
        rows.reverse()
        for row in rows:
            row["cal_date"] = row["cal_date"].replace("-", "")
            row["pretrade_date"] = "invalid_unused"
        rows.append({"exchange": "SZSE", "cal_date": "20261003", "is_open": 0,
                     "pretrade_date": "invalid_unused"})
        pd.DataFrame(rows).to_parquet(path, index=False)
        result = self.capture([path], format="tushare_parquet")
        self.assertEqual(result["calendar"]["sessions"], [
            {"session": "2026-10-03", "close_at": "2026-10-03T15:00:00+08:00"}])
        self.assertEqual(result["source_files"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(self.calendar.validate_calendar_capture(result), result)

    def test_parquet_selected_invalid_row_and_missing_columns_rejected(self):
        import pandas as pd
        path = self.root / "calendar.parquet"
        for rows in [[{"exchange": "SSE", "cal_date": "20261001", "is_open": True}],
                     [{"exchange": "SSE", "cal_date": "20261001", "is_open": 1.0}],
                     [{"exchange": "SSE", "cal_date": "20261001"}]]:
            pd.DataFrame(rows).to_parquet(path, index=False)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.capture([path], format="tushare_parquet", coverage_end="2026-10-01")


if __name__ == "__main__":
    unittest.main()
