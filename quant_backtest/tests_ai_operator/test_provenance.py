"""本地日线来源快照的字节绑定和接收时间契约测试。"""

import copy
import hashlib
import importlib
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ai_operator.contracts import canonical_json, normalize_time


def bar(session="2026-09-30", **changes):
    result = {"session": session, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 0}
    result.update(changes)
    return result


def rehash(value):
    value["snapshot_id"] = hashlib.sha256(canonical_json({k: v for k, v in value.items()
                                                            if k != "snapshot_id"}).encode("utf-8")).hexdigest()
    return value


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.provenance"), "缺少行情来源快照模块")
        self.module = importlib.import_module("ai_operator.provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "daily.json"
        self.path.write_text(json.dumps({"bars": [bar()]}), encoding="utf-8")

    def capture(self, **changes):
        args = {"source_id": "local", "code": "000001.SH", "price_basis": "raw", "revision": "r1"}
        args.update(changes)
        return self.module.capture_snapshot(self.path, **args)

    def write(self, rows):
        self.path.write_text(json.dumps({"bars": rows}), encoding="utf-8")

    def test_capture_uses_current_read_time_and_preserves_zero_volume(self):
        os.utime(self.path, (1, 1))
        before = datetime.now(timezone.utc)
        result = self.capture()
        after = datetime.now(timezone.utc)
        received = datetime.fromisoformat(result["received_at"])
        self.assertLessEqual(before, received)
        self.assertLessEqual(received, after)
        self.assertEqual(result["bars"][0]["available_at"], result["received_at"])
        self.assertEqual(result["bars"][0]["close_at"], "2026-09-30T15:00:00+08:00")
        self.assertEqual(result["bars"][0]["volume"], 0)
        self.assertEqual((result["historical_availability"], result["supplier_verification"]), ("unknown", "unverified"))
        self.assertIs(result["production_eligible"], False)

    def test_capture_has_no_caller_time_parameters(self):
        signature = inspect.signature(self.module.capture_snapshot)
        self.assertNotIn("received_at", signature.parameters)
        self.assertNotIn("available_at", signature.parameters)
        for field in ["received_at", "available_at"]:
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.capture(**{field: "2025-01-01T00:00:00+00:00"})

    def test_unsorted_bars_sort_and_share_actual_batch_time(self):
        self.write([bar("2026-09-30"), bar("2026-09-29")])
        result = self.capture()
        self.assertEqual([row["session"] for row in result["bars"]], ["2026-09-29", "2026-09-30"])
        self.assertEqual({row["available_at"] for row in result["bars"]}, {result["received_at"]})

    def test_source_file_hash_binds_exact_bytes(self):
        raw = self.path.read_bytes()
        result = self.capture()
        self.assertEqual(result["source_file"], {"path": str(self.path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                                                "size_bytes": len(raw), "format": "json"})
        self.assertEqual(result["snapshot_id"], rehash(copy.deepcopy(result))["snapshot_id"])

    def test_content_and_hash_use_same_read_even_when_file_changes_after_read(self):
        original = self.path.read_bytes()

        def after_read():
            self.write([bar(close=10)])
            return "2026-10-07T00:00:00+00:00"

        with patch("ai_operator.contracts.utc_now", side_effect=after_read):
            result = self.capture()
        self.assertEqual(result["bars"][0]["close"], 11)
        self.assertEqual(result["source_file"]["sha256"], hashlib.sha256(original).hexdigest())

    def test_strict_json_rejects_duplicates_extra_fields_and_nonfinite(self):
        valid = json.dumps(bar())
        cases = ['{"bars":[],"bars":[]}', '{"bars":[' + valid.replace('"close": 11', '"close": 11,"close": 11') + ']}',
                 '{"bars":[' + valid.replace('"close": 11', '"close": NaN') + ']}',
                 '{"bars":[],"received_at":"2020-01-01T00:00:00Z"}', '[]', '{"bars":[true]}', '{"bars":null}']
        for raw in cases:
            self.path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.capture()

    def test_invalid_bars_reject_ohlc_bool_dates_duplicates_and_empty(self):
        variants = [[bar(close=13)], [bar(open=8)], [bar(low=13)], [bar(high=0)], [bar(volume=-1)],
                    [bar(open=True)], [bar(volume=False)], [bar(close="11")], [bar(open=float("inf"))],
                    [bar("2026-9-30")], [bar("2026-02-30")], [bar(extra=1)], [bar(), bar()], []]
        for rows in variants:
            self.write(rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.capture()

    def test_future_close_rejected_and_exact_close_allowed(self):
        with patch("ai_operator.contracts.utc_now", return_value="2026-09-30T06:59:59+00:00"):
            with self.assertRaises(ValueError):
                self.capture()
        with patch("ai_operator.contracts.utc_now", return_value="2026-09-30T07:00:00+00:00"):
            self.assertEqual(self.capture()["bars"][0]["session"], "2026-09-30")

    def test_invalid_metadata_rejected(self):
        for changes in [{"source_id": " "}, {"revision": ""}, {"code": "000001"}, {"code": "０００００１.SH"},
                        {"code": "000001.HK"}, {"price_basis": "qfq"}, {"format": "csv"}, {"source_id": True}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.capture(**changes)

    def test_maximum_bars_limit(self):
        self.write([bar()] * 5001)
        with self.assertRaises(ValueError):
            self.capture()

    def test_regular_file_and_size_limit(self):
        with self.assertRaises(ValueError):
            self.module.capture_snapshot(self.path.parent, source_id="s", code="000001.SH", price_basis="raw", revision="r")
        with self.path.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self.capture()

    def test_remote_and_device_paths_rejected_before_filesystem_access(self):
        paths = [r"\\server\share\daily.json", "//server/share/daily.json", r"\\?\UNC\server\share\daily.json",
                 r"\\.\pipe\remote", r"\\?\C:\daily.json", r"\??\C:\daily.json"]
        for path in paths:
            with self.subTest(path=path), patch("ai_operator.provenance.os.lstat", side_effect=AssertionError("不能访问远端路径")), \
                    patch("builtins.open", side_effect=AssertionError("不能打开远端路径")), self.assertRaises(ValueError):
                self.module.capture_snapshot(path, source_id="s", code="000001.SH", price_basis="raw", revision="r")

    def test_validator_rejects_remote_and_device_source_paths(self):
        for path in [r"\\server\share\daily.json", "//server/share/daily.json", r"\\?\UNC\server\share\daily.json",
                     r"\\.\pipe\remote", r"\\?\C:\daily.json", r"\??\C:\daily.json"]:
            value = self.capture()
            value["source_file"]["path"] = path
            rehash(value)
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.module.validate_snapshot(value)

    def test_symlink_rejected(self):
        link = self.path.parent / "link.json"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("当前 Windows 会话不允许创建符号链接")
        with self.assertRaises(ValueError):
            self.module.capture_snapshot(link, source_id="s", code="000001.SH", price_basis="raw", revision="r")

    @unittest.skipUnless(os.name == "nt", "仅 Windows 提供目录 junction")
    def test_windows_parent_junction_rejected(self):
        import _winapi
        target = self.path.parent / "target"
        target.mkdir()
        (target / "daily.json").write_bytes(self.path.read_bytes())
        junction = self.path.parent / "alias"
        _winapi.CreateJunction(str(target), str(junction))
        self.addCleanup(os.rmdir, junction)
        with self.assertRaises(ValueError):
            self.module.capture_snapshot(junction / "daily.json", source_id="s", code="000001.SH", price_basis="raw", revision="r")

    def test_validate_deep_copies_and_does_not_read_source_again(self):
        result = self.capture()
        original = copy.deepcopy(result)
        self.path.unlink()
        validated = self.module.validate_snapshot(result)
        self.assertEqual(validated, original)
        self.assertEqual(result, original)
        validated["bars"][0]["close"] = 9
        self.assertEqual(result["bars"][0]["close"], 11)

    def test_validate_rejects_changed_content_without_hash(self):
        result = self.capture()
        result["bars"][0]["close"] = 10
        with self.assertRaises(ValueError):
            self.module.validate_snapshot(result)

    def test_validate_rejects_semantic_forgery_even_when_rehashed(self):
        variants = [{"schema_version": True}, {"period": "1m"}, {"kind": "other"}, {"historical_availability": "trusted"},
                    {"supplier_verification": "verified"}, {"production_eligible": 0}, {"extra": 1}, {"code": "000001.HK"}]
        for changes in variants:
            value = self.capture()
            value.update(changes)
            rehash(value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.validate_snapshot(value)

    def test_validate_rejects_backdated_availability_and_incorrect_close(self):
        for changes in [{"available_at": "2026-09-30T07:00:00+00:00"}, {"close_at": "2026-09-30T14:00:00+08:00"},
                        {"close_at": "2026-09-29T15:00:00+08:00"}, {"volume": True}, {"extra": 1}]:
            value = self.capture()
            value["bars"][0].update(changes)
            rehash(value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.validate_snapshot(value)

    def test_validate_normalizes_equivalent_timezones_and_preserves_local_close_date(self):
        value = self.capture()
        original = copy.deepcopy(value)
        value["received_at"] = datetime.fromisoformat(value["received_at"]).astimezone(timezone.utc).isoformat()
        value["bars"][0]["close_at"] = normalize_time(value["bars"][0]["close_at"])
        rehash(value)
        normalized = self.module.validate_snapshot(value)
        self.assertEqual(normalized, original)

    def test_validate_rejects_invalid_source_metadata(self):
        for changes in [{"sha256": "bad"}, {"size_bytes": True}, {"size_bytes": 0}, {"size_bytes": 32 * 1024 * 1024 + 1},
                        {"format": "csv"}, {"path": "relative.json"}, {"extra": 1}]:
            value = self.capture()
            value["source_file"].update(changes)
            rehash(value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.validate_snapshot(value)

    def test_parquet_selects_security_and_binds_unused_columns(self):
        import pandas as pd
        path = self.path.with_suffix(".parquet")
        pd.DataFrame([dict(bar(), ts_code="000001.SH", trade_date="20260930", extra=float("nan")),
                      dict(bar(close=10), ts_code="000002.SZ", trade_date="20260930", extra=2)]).drop(columns="session").to_parquet(path)
        result = self.module.capture_snapshot(path, source_id="local", code="000001.SH", price_basis="qfq_snapshot", revision="r", format="ashare_parquet")
        self.assertEqual(len(result["bars"]), 1)
        self.assertEqual(result["bars"][0]["close"], 11)
        self.assertEqual(set(result["bars"][0]), {"session", "open", "high", "low", "close", "volume", "close_at", "available_at"})
        self.assertEqual(result["source_file"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(ValueError):
            self.module.capture_snapshot(path, source_id="local", code="000003.SZ", price_basis="raw", revision="r", format="ashare_parquet")

    def test_parquet_accepts_vol_and_rejects_bad_selected_rows(self):
        import pandas as pd
        path = self.path.with_suffix(".parquet")
        row = dict(bar(), ts_code="000001.SH", trade_date="20260930")
        row["vol"] = row.pop("volume")
        row.pop("session")
        pd.DataFrame([row]).to_parquet(path)
        args = {"source_id": "local", "code": "000001.SH", "price_basis": "raw", "revision": "r", "format": "ashare_parquet"}
        self.assertEqual(self.module.capture_snapshot(path, **args)["bars"][0]["volume"], 0)
        for changes in [{"low": 20}, {"trade_date": "20260931"}, {"close": float("nan")}]:
            pd.DataFrame([dict(row, **changes)]).to_parquet(path)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.capture_snapshot(path, **args)


if __name__ == "__main__":
    unittest.main()
