"""离线包核查的安全与一致性行为测试。"""

import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib
import ai_operator.bundles as bundle_module

try:
    from ai_operator.bundles import inspect_bundle
except ImportError:
    inspect_bundle = None


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(inspect_bundle, "任务 C 尚未提供 inspect_bundle")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_zip(self, entries):
        target = self.root / "fixture.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return target

    def manifest(self, entries, **overrides):
        value = {"bundle_version": "fixture-1", "schema_version": 1,
                 "files": {name: hashlib.sha256(data).hexdigest()
                           for name, data in entries}}
        value.update(overrides)
        return json.dumps(value).encode("utf-8")

    def assert_invalid(self, result, code=None):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["production_eligible"])
        if code:
            self.assertIn(code, [item["code"] for item in result["errors"]])

    def test_missing_manifest_is_unverified_and_never_production(self):
        result = inspect_bundle(self.write_zip([("readme.txt", b"hello")]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["schema_verified"])
        self.assertEqual(result["files"][0]["sha256"],
                         "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        self.assertEqual(result["stats"]["total_bytes"], 5)

    def test_consistent_manifest_checks_local_envelope_only(self):
        entries = [("rules/rules.json", b'[{"id":"V011"},{"rule_id":"V012"}]'),
                   ("data/courses.csv", b"course_id,title\nV011,a\nV012,b\n")]
        target = self.write_zip(entries + [("manifest.json", self.manifest(entries))])
        before = target.read_bytes()
        result = inspect_bundle(target)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["schema_verified"])
        self.assertFalse(result["production_eligible"])
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_zip_and_directory_have_same_content_hash_without_writes(self):
        entries = [("data.txt", b"payload")]
        entries.append(("manifest.json", self.manifest(entries)))
        target = self.write_zip(entries)
        folder = self.root / "folder"
        folder.mkdir()
        for name, data in entries:
            (folder / name).write_bytes(data)
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        first, second = inspect_bundle(target), inspect_bundle(folder)
        self.assertEqual(first["source_hash"], second["source_hash"])
        self.assertEqual(second["status"], "verified")
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)

    def test_unsafe_zip_paths_are_rejected(self):
        for name in ["../out", "/absolute", "C:/out", "C:out", "a/../out",
                     "a\\..\\out", "\\server\\share", "a/./b", "a//b",
                     "a:stream", "dir /file", "file."]:
            with self.subTest(name=name):
                self.assert_invalid(inspect_bundle(self.write_zip([(name, b"x")])),
                                    "unsafe_path")

    def test_duplicate_case_and_file_directory_collisions_are_rejected(self):
        for names in [("RULES/x", "rules/X"), ("a", "a/b"), ("a/", "A")]:
            with self.subTest(names=names):
                self.assert_invalid(inspect_bundle(self.write_zip([(n, b"") for n in names])),
                                    "duplicate_path")

    def test_zip_symlink_and_special_file_are_rejected(self):
        for mode in [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600]:
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = mode << 16
            self.assert_invalid(inspect_bundle(self.write_zip([(entry, b"outside")])),
                                "unsafe_type")

    def test_directory_symlink_is_rejected(self):
        folder = self.root / "folder"
        folder.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        try:
            os.symlink(outside, folder / "link.txt")
        except OSError as error:
            self.skipTest(f"当前系统不允许创建符号链接：{error}")
        self.assert_invalid(inspect_bundle(folder), "unsafe_type")

    def test_root_symlink_is_rejected(self):
        target = self.write_zip([("x", b"x")])
        alias = self.root / "alias.zip"
        try:
            os.symlink(target, alias)
        except OSError as error:
            self.skipTest(f"当前系统不允许创建符号链接：{error}")
        self.assert_invalid(inspect_bundle(alias), "unsafe_type")

    @unittest.skipUnless(os.name == "nt", "仅 Windows 提供 junction fixture")
    def test_directory_junction_and_root_junction_are_rejected(self):
        import _winapi
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        folder = self.root / "folder"
        folder.mkdir()
        junction = folder / "junction"
        _winapi.CreateJunction(str(outside), str(junction))
        self.assert_invalid(inspect_bundle(folder), "unsafe_type")
        self.assert_invalid(inspect_bundle(junction), "unsafe_type")
        self.assertEqual((outside / "secret.txt").read_bytes(), b"secret")

    def test_corrupt_member_crc_is_rejected(self):
        target = self.write_zip([("x", b"data")])
        raw = bytearray(target.read_bytes())
        central = raw.index(b"PK\x01\x02")
        raw[central + 16:central + 20] = b"\x00" * 4
        target.write_bytes(raw)
        self.assert_invalid(inspect_bundle(target), "source_error")

    def test_corrupt_deflate_stream_is_structured_invalid_without_writes(self):
        target = self.write_zip([("x.txt", b"hello world " * 100)])
        raw = bytearray(target.read_bytes())
        offset = 30 + struct.unpack_from("<H", raw, 26)[0] + struct.unpack_from("<H", raw, 28)[0]
        raw[offset] = 7
        target.write_bytes(raw)
        with zipfile.ZipFile(target) as archive:
            with self.assertRaises(zlib.error):
                archive.read("x.txt")
        result = inspect_bundle(target)
        self.assertIs(type(result), dict)
        self.assert_invalid(result, "source_error")
        self.assertIsInstance(json.dumps(result, allow_nan=False), str)
        self.assertEqual(target.read_bytes(), bytes(raw))

    def test_corrupt_lzma_stream_is_structured_invalid(self):
        target = self.root / "lzma.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_LZMA) as archive:
            archive.writestr("x.txt", b"hello world " * 100)
        raw = bytearray(target.read_bytes())
        offset = 30 + struct.unpack_from("<H", raw, 26)[0] + struct.unpack_from("<H", raw, 28)[0]
        properties_size = struct.unpack_from("<H", raw, offset + 2)[0]
        raw[offset + 4 + properties_size:offset + 9 + properties_size] = b"\xff" * 5
        target.write_bytes(raw)
        with zipfile.ZipFile(target) as archive:
            with self.assertRaises(lzma.LZMAError):
                archive.read("x.txt")
        self.assert_invalid(inspect_bundle(target), "source_error")

    def test_directory_size_limit_and_zip_boundary(self):
        folder = self.root / "folder"
        folder.mkdir()
        data = b"x" * (8 * 1024 * 1024)
        (folder / "large").write_bytes(data + b"x")
        self.assert_invalid(inspect_bundle(folder), "limit_exceeded")
        result = inspect_bundle(self.write_zip([("large", data)]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stats"]["total_bytes"], 8 * 1024 * 1024)

    def test_same_length_rewrite_after_read_is_reported(self):
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "a").write_bytes(b"old")
        (folder / "b").write_bytes(b"two")
        real_open = os.open
        def rewrite_after_first(path, flags):
            if Path(path).name == "b":
                (folder / "a").write_bytes(b"new")
            return real_open(path, flags)
        with patch.object(os, "open", rewrite_after_first):
            self.assert_invalid(inspect_bundle(folder), "source_changed")

    def test_zip_growth_after_open_is_still_bounded(self):
        target = self.write_zip([("x", b"x")])
        raw = target.read_bytes()
        real_open = bundle_module._open_regular
        def grow_after_open(path):
            stream, before = real_open(path)
            with target.open("r+b") as writer:
                writer.seek(128 * 1024 * 1024 + 1)
                writer.write(raw)
            return stream, before
        with patch.object(bundle_module, "_open_regular", grow_after_open):
            self.assert_invalid(inspect_bundle(target), "limit_exceeded")

    def test_file_count_limit(self):
        self.assert_invalid(inspect_bundle(self.write_zip([(str(n), b"") for n in range(2001)])),
                            "limit_exceeded")

    def test_single_file_limit(self):
        self.assert_invalid(inspect_bundle(self.write_zip([("large", b"a" * (8 * 1024 * 1024 + 1))])),
                            "limit_exceeded")

    def test_total_uncompressed_limit(self):
        data = b"a" * (8 * 1024 * 1024)
        self.assert_invalid(inspect_bundle(self.write_zip([(str(n), data) for n in range(9)])),
                            "limit_exceeded")

    def test_actual_directory_reads_enforce_size_limit(self):
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "large").write_bytes(b"a" * (8 * 1024 * 1024 + 1))
        real_entries = bundle_module._directory_entries
        def understated(path):
            return [(name, 1, value) for name, size, value in real_entries(path)]
        with patch.object(bundle_module, "_directory_entries", understated):
            self.assert_invalid(inspect_bundle(folder), "limit_exceeded")

    def test_manifest_hash_missing_and_extra_files_are_errors(self):
        entries = [("a.txt", b"a"), ("b.txt", b"b")]
        cases = [{"a.txt": "0" * 64, "b.txt": hashlib.sha256(b"b").hexdigest()},
                 {"a.txt": hashlib.sha256(b"a").hexdigest()},
                 {"a.txt": hashlib.sha256(b"a").hexdigest(),
                  "b.txt": hashlib.sha256(b"b").hexdigest(), "ghost": "0" * 64}]
        for files in cases:
            with self.subTest(files=files):
                self.assert_invalid(inspect_bundle(self.write_zip(entries + [
                    ("manifest.json", self.manifest(entries, files=files))])), "manifest_mismatch")

    def test_invalid_manifest_fields_and_duplicate_json_keys(self):
        for data in [b'{"schema_version":1,"bundle_version":"a","files":[]}',
                     b'{"schema_version":1,"bundle_version":"","files":{}}',
                     b'{"files":{},"files":{}}', b'{"files":NaN}',
                     b'{"bundle_version":"x","schema_version":1e309,"files":{}}']:
            with self.subTest(data=data):
                self.assert_invalid(inspect_bundle(self.write_zip([("manifest.json", data)])))

    def test_ragged_csv_rows_are_errors(self):
        for data in [b"course_id,title\nV011,a,extra\nV012,b\n",
                     b"course_id,title\nV011\nV012,b\n"]:
            with self.subTest(data=data):
                entries = [("data/courses.csv", data)]
                result = inspect_bundle(self.write_zip(entries + [
                    ("manifest.json", self.manifest(entries))]))
                self.assert_invalid(result, "csv_invalid")

    def test_unknown_manifest_schema_is_unverified(self):
        for version in [2, "custom", True, None]:
            with self.subTest(version=version):
                result = inspect_bundle(self.write_zip([
                    ("manifest.json", self.manifest([], schema_version=version))]))
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["status"], "unverified")

    def test_string_manifest_schema_one_is_recognized(self):
        result = inspect_bundle(self.write_zip([
            ("manifest.json", self.manifest([], schema_version="1"))]))
        self.assertEqual(result["status"], "verified")

    def test_rules_duplicate_identifiers_and_ambiguous_alias_are_errors(self):
        for data in [b'[{"id":"V011"},{"rule_id":"V011"}]',
                     b'[{"id":"a","rule_id":"b"}]']:
            with self.subTest(data=data):
                entries = [("rules/rules.json", data)]
                result = inspect_bundle(self.write_zip(entries + [
                    ("manifest.json", self.manifest(entries))]))
                self.assert_invalid(result, "rules_invalid")

    def test_unknown_rules_structures_are_unverified(self):
        for data in [b'{"entries":[]}', b'["V011"]', b'[{"other":1}]']:
            with self.subTest(data=data):
                entries = [("rules/rules.json", data)]
                result = inspect_bundle(self.write_zip(entries + [
                    ("manifest.json", self.manifest(entries))]))
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["status"], "unverified")

    def test_unknown_csv_columns_warn_and_do_not_claim_verification(self):
        entries = [("data/courses.csv", b"mystery,label\nV011,a\n")]
        result = inspect_bundle(self.write_zip(entries + [("manifest.json", self.manifest(entries))]))
        self.assertEqual(result["status"], "unverified")
        self.assertIn("csv_unknown_columns", [w["code"] for w in result["warnings"]])

    def test_known_csv_column_checks_course_codes(self):
        entries = [("data/courses.csv", b"course_id,title\nV011,a\nV012,b\n")]
        result = inspect_bundle(self.write_zip(entries + [("manifest.json", self.manifest(entries))]))
        self.assertEqual(result["stats"]["course_codes"], ["V011", "V012"])

    def test_missing_and_broken_zip_are_structured_failures(self):
        missing = self.root / "missing.zip"
        self.assert_invalid(inspect_bundle(missing), "source_error")
        self.assertFalse(missing.exists())
        broken = self.root / "broken.zip"
        broken.write_bytes(b"PK not a zip")
        self.assert_invalid(inspect_bundle(broken), "source_error")

    def test_all_results_are_json_serializable(self):
        result = inspect_bundle(self.write_zip([("x", b"x")]))
        self.assertIsInstance(json.dumps(result, allow_nan=False), str)


if __name__ == "__main__":
    unittest.main()
