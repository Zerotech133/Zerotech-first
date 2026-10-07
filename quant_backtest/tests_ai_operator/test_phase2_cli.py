"""验证第二批入口的时点契约、幂等审计和失败无写入。"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Phase2CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.database = self.directory / "memory.sqlite3"
        self.input = self.directory / "input.json"

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        return subprocess.run(
            [sys.executable, "-B", "-m", "ai_operator", *map(str, args)],
            cwd=self.directory, env=env, text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )

    def request(self):
        return {
            "code": "600000.SH", "period": "1d",
            "as_of": "2026-10-07T08:00:00Z",
            "facts": [{"id": "f1", "source": "fixture", "occurred_at": "2026-10-07T07:00:00Z",
                       "available_at": "2026-10-07T07:01:00Z", "data": {"close": 10.0}}],
            "versions": {"data": "fixture-v1", "algorithm": "local-v1", "knowledge": "none", "rules": "none"},
        }

    def write_input(self, value):
        self.input.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_fact_pack_is_readonly_and_has_stable_hash(self):
        self.write_input(self.request())
        before = self.input.read_bytes()
        first = self.run_cli("fact-pack", "--input", self.input)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        pack = json.loads(first.stdout)
        self.assertEqual(pack["environment"], "shadow")
        self.assertEqual(len(pack["fact_hash"]), 64)
        second = self.run_cli("fact-pack", "--input", self.input)
        self.assertEqual(json.loads(second.stdout), pack)
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.input])

    def test_shadow_decision_records_two_events_and_retries(self):
        self.write_input(self.request())
        packed = self.run_cli("fact-pack", "--input", self.input)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.write_input({"pack": json.loads(packed.stdout), "action": "candidate_buy",
                          "evidence_ids": ["f1"], "rationale": "仅作影子研究候选",
                          "valid_until": "2026-10-08T08:00:00Z"})
        initialized = self.run_cli("init", "--db", self.database)
        self.assertEqual(initialized.returncode, 0, initialized.stdout)
        args = ("shadow-decision", "--input", self.input, "--db", self.database)
        first = self.run_cli(*args)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.run_cli(*args)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        result = json.loads(self.run_cli("snapshot", "--db", self.database).stdout)
        self.assertEqual(result["events"], 2)
        self.assertEqual(result["lessons"], 0)

    def test_shadow_missing_database_does_not_initialize(self):
        self.write_input({})
        result = self.run_cli("shadow-decision", "--input", self.input, "--db", self.database)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["type"], "FileNotFoundError")
        self.assertFalse(self.database.exists())

    def test_shadow_empty_database_is_not_initialized(self):
        self.write_input(self.request())
        packed = self.run_cli("fact-pack", "--input", self.input)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.write_input({"pack": json.loads(packed.stdout), "action": "abstain",
                          "evidence_ids": [], "rationale": "仅核查空库拒绝",
                          "valid_until": "2026-10-08T08:00:00Z"})
        self.database.touch()
        result = self.run_cli("shadow-decision", "--input", self.input, "--db", self.database)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(self.database.read_bytes(), b"")
        self.assertFalse(Path(str(self.database) + "-wal").exists())

    def test_invalid_fact_request_is_json_error(self):
        cases = [[], {}, dict(self.request(), unexpected=True)]
        future = self.request()
        future["facts"][0]["available_at"] = "2026-10-08T08:00:00Z"
        cases.append(future)
        for value in cases:
            with self.subTest(value=value):
                self.write_input(value)
                result = self.run_cli("fact-pack", "--input", self.input)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertEqual(result.stderr, "")
                self.assertFalse(self.database.exists())

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        for raw in ('{"code":"A","code":"B"}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw):
                self.input.write_text(raw, encoding="utf-8")
                result = self.run_cli("fact-pack", "--input", self.input)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertIn("JSON", json.loads(result.stdout)["error"]["message"])

    def test_replay_empty_list_without_data_dependency(self):
        self.write_input([])
        result = self.run_cli("replay", "--input", self.input)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replay = json.loads(result.stdout)
        self.assertEqual(replay["bar_count"], 0)
        self.assertEqual(replay["snapshots"], [])
        self.assertFalse(self.database.exists())

    def test_replay_forwards_input_and_algorithm_id(self):
        from ai_operator.cli import main
        self.write_input([])
        output = io.StringIO()
        with patch("ai_operator.replay.replay_bars", return_value={"bar_count": 0}) as replay:
            with redirect_stdout(output):
                status = main(["replay", "--input", str(self.input), "--algorithm-id", "test-v1"])
        self.assertEqual(status, 0)
        replay.assert_called_once_with([], algorithm_id="test-v1")

    def test_invalid_decision_keeps_event_count(self):
        self.assertEqual(self.run_cli("init", "--db", self.database).returncode, 0)
        self.write_input({"pack": {}, "action": "candidate_buy", "evidence_ids": [],
                          "rationale": "无事实", "valid_until": "2026-10-08T08:00:00Z"})
        result = self.run_cli("shadow-decision", "--input", self.input, "--db", self.database)
        self.assertEqual(result.returncode, 2)
        snapshot = json.loads(self.run_cli("snapshot", "--db", self.database).stdout)
        self.assertEqual(snapshot["events"], 0)


if __name__ == "__main__":
    unittest.main()
