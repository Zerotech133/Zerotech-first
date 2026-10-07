"""验证命令行的显式写入、只读查询及错误退出。"""

import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
import io
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.database = self.directory / "memory.sqlite3"

    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-B", "-m", "ai_operator", *map(str, args)],
            cwd=self.directory,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )

    def initialize(self):
        result = self.run_cli("init", "--db", self.database)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_help_has_no_filesystem_side_effect(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("import-legacy", result.stdout)
        self.assertIn("多 AI 记忆审计与离线资料核查", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_database_path_is_required(self):
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.database.exists())

    def test_argument_errors_are_json_without_filesystem_side_effects(self):
        cases = (
            (),
            ("unknown-command",),
            ("init", "--db", self.database, "--unknown-option"),
            ("recall", "--db", self.database, "--environment", "production", "--as-of", "2026-10-07T08:00:00Z"),
            ("recall", "--db", self.database, "--environment", "shadow", "--as-of", "2026-10-07T08:00:00Z", "--limit", "invalid"),
        )
        for args in cases:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                value = json.loads(result.stdout)
                self.assertFalse(value["ok"])
                self.assertTrue(value["error"]["message"])
                self.assertEqual(result.stderr, "")
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_corrupt_deflate_returns_invalid_json_and_exit_two(self):
        target = self.directory / "corrupt.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("x.txt", b"hello world " * 100)
        raw = bytearray(target.read_bytes())
        offset = 30 + struct.unpack_from("<H", raw, 26)[0] + struct.unpack_from("<H", raw, 28)[0]
        raw[offset] = 7
        target.write_bytes(raw)
        result = self.run_cli("inspect-bundle", "--path", target)
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "invalid")
        self.assertTrue(value["errors"])
        self.assertEqual(result.stderr, "")
        self.assertEqual(target.read_bytes(), bytes(raw))

    def test_missing_snapshot_does_not_create_database(self):
        result = self.run_cli("snapshot", "--db", self.database)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_initialize_and_readonly_snapshot(self):
        initialized = self.initialize()
        self.assertEqual(initialized["events"], 0)
        before = self.database.read_bytes()
        result = self.run_cli("snapshot", "--db", self.database)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["events"], 0)
        self.assertEqual(self.database.read_bytes(), before)

    def test_recall_requires_environment_and_time(self):
        self.initialize()
        result = self.run_cli("recall", "--db", self.database)
        self.assertEqual(result.returncode, 2)

    def test_recall_empty_and_invalid_time(self):
        self.initialize()
        args = ["recall", "--db", self.database, "--environment", "shadow"]
        valid = self.run_cli(*args, "--as-of", "2026-10-07T08:00:00+00:00")
        self.assertEqual(valid.returncode, 0, valid.stderr + valid.stdout)
        self.assertEqual(json.loads(valid.stdout), [])
        invalid = self.run_cli(*args, "--as-of", "2026-10-07")
        self.assertEqual(invalid.returncode, 2)

    def test_import_then_repeat_is_idempotent(self):
        reports = self.directory / "reports"
        reports.mkdir()
        record = {"ts": "2026-10-03 18:38:50", "sid": "9dim", "code": "600663.SH"}
        source = reports / "council_proposals.jsonl"
        source.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        before = source.read_bytes()
        first = self.run_cli("import-legacy", "--db", self.database, "--reports-dir", reports)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(json.loads(first.stdout)["inserted"], 1)
        second = self.run_cli("import-legacy", "--db", self.database, "--reports-dir", reports)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(json.loads(second.stdout)["inserted"], 0)
        self.assertEqual(source.read_bytes(), before)

    def test_bad_import_directory_fails_before_creating_database(self):
        result = self.run_cli(
            "import-legacy", "--db", self.database,
            "--reports-dir", self.directory / "missing",
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())

    def test_export_requires_existing_database(self):
        destination = self.directory / "export"
        result = self.run_cli("export", "--db", self.database, "--output-dir", destination)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())
        self.assertFalse(destination.exists())

    def test_bundle_inspection_missing_file_fails_without_database(self):
        result = self.run_cli("inspect-bundle", "--path", self.directory / "missing.zip")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse(self.database.exists())

    def test_source_changed_returns_structured_retryable_failure(self):
        from ai_operator.cli import main

        reports = self.directory / "reports"
        reports.mkdir()
        output = io.StringIO()
        with patch("ai_operator.legacy.ingest_legacy", side_effect=RuntimeError("来源文件在读取期间被改写，请重试导入")):
            with redirect_stdout(output):
                code = main(["import-legacy", "--db", str(self.database), "--reports-dir", str(reports)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
