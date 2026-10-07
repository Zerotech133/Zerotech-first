"""验证成熟标签与数据集 CLI 的只读联调、时点边界和错误输出。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Phase3CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.input = self.directory / "request.json"

    def run_cli(self, command):
        return subprocess.run([sys.executable, "-B", "-m", "ai_operator", command, "--input", str(self.input)],
                              cwd=self.directory, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                              text=True, encoding="utf-8", capture_output=True, timeout=30)

    def write_input(self, value):
        self.input.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def outcome_request(self):
        from ai_operator.shadow import build_fact_pack, make_shadow_decision
        pack = build_fact_pack("600000.SH", "1d", "2026-10-06T08:00:00Z",
                               [{"id": "fixture", "source": "测试夹具", "occurred_at": "2026-10-06T07:00:00Z",
                                 "available_at": "2026-10-06T07:01:00Z", "data": {"close": 10}}],
                               {key: "fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
        decision = make_shadow_decision(pack, "observe", ["fixture"], "离线联调夹具", "2026-10-10T00:00:00Z")
        sessions = [{"session": f"2026-10-{day:02d}", "close_at": f"2026-10-{day:02d}T15:00:00+08:00"}
                    for day in (7, 8, 9)]
        return {"pack": pack, "decision": decision,
                "bars": [dict(s, close=10 + index, available_at=s["close_at"])
                         for index, s in enumerate(sessions)],
                "calendar": {"version": "fixture-calendar-v1", "sessions": sessions},
                "source": {"source_id": "fixture", "source_hash": "a" * 64,
                           "price_basis": "raw", "availability_status": "trusted"},
                "evaluated_at": "2026-10-09T08:00:00Z", "horizons": [1]}

    def split_request(self):
        rows = []
        for index, day in enumerate((2, 12, 22)):
            rows.append({"sample_id": f"s{index}", "code": f"00000{index}.SZ", "content_family_id": None,
                         "decision_at": f"2026-01-{day:02d}T07:00:00Z",
                         "feature_available_at": f"2026-01-{day:02d}T06:00:00Z",
                         "label_end_at": f"2026-01-{day + 1:02d}T07:00:00Z",
                         "label_available_at": f"2026-01-{day + 1:02d}T08:00:00Z",
                         "features": {"pivot_count": index}, "target": index / 100,
                         "target_kind": "forward_close_return", "horizon": 5, "label_policy": "fixture-v1"})
        return {"rows": rows, "feature_allowlist": ["pivot_count"],
                "train_end": "2026-01-10T00:00:00Z", "validation_end": "2026-01-20T00:00:00Z",
                "test_end": "2026-01-30T00:00:00Z", "as_of": "2026-01-31T00:00:00Z", "embargo_seconds": 3600}

    def test_outcome_readonly_with_mature_target(self):
        self.write_input(self.outcome_request())
        before = self.input.read_bytes()
        result = self.run_cli("outcome-label")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["labels"][0]["status"], "matured")
        self.assertAlmostEqual(value["labels"][0]["target"], 0.1)
        self.assertFalse(value["production_eligible"])
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.input])

    def test_outcome_unknown_source_does_not_produce_target(self):
        value = self.outcome_request()
        value["source"]["availability_status"] = "unknown"
        self.write_input(value)
        result = self.run_cli("outcome-label")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        label = json.loads(result.stdout)["labels"][0]
        self.assertEqual(label["status"], "audit_only")
        self.assertIsNone(label["target"])

    def test_split_and_summary_readonly_and_repeatable(self):
        self.write_input(self.split_request())
        first = self.run_cli("dataset-split")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        split = json.loads(first.stdout)
        second = self.run_cli("dataset-split")
        self.assertEqual(json.loads(second.stdout), split)
        self.assertEqual([len(split[name]) for name in ("train", "validation", "test")], [1, 1, 1])
        self.write_input(split)
        summary = self.run_cli("dataset-summary")
        self.assertEqual(summary.returncode, 0, summary.stdout + summary.stderr)
        value = json.loads(summary.stdout)
        self.assertEqual(value["partitions"]["test"]["n"], 1)
        self.assertAlmostEqual(value["partitions"]["test"]["mean_target"], 0.02)
        self.assertEqual(list(self.directory.iterdir()), [self.input])

    def test_summary_rejects_tampered_hash(self):
        self.write_input(self.split_request())
        split_result = self.run_cli("dataset-split")
        self.assertEqual(split_result.returncode, 0, split_result.stdout + split_result.stderr)
        split = json.loads(split_result.stdout)
        split["test"][0]["target"] = 99
        self.write_input(split)
        result = self.run_cli("dataset-summary")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_unknown_fields_invalid_json_and_nonfinite_errors(self):
        for command in ("outcome-label", "dataset-split", "dataset-summary"):
            for raw in ('{}', '[]', '{"x":NaN}', '{"x":1,"x":2}', '{'):
                with self.subTest(command=command, raw=raw):
                    self.input.write_text(raw, encoding="utf-8")
                    result = self.run_cli(command)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse(json.loads(result.stdout)["ok"])
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(list(self.directory.iterdir()), [self.input])

    def test_required_input_and_help_have_no_side_effects(self):
        for command in ("outcome-label", "dataset-split", "dataset-summary"):
            for flag, expected in (("--help", 0), (None, 2)):
                args = [sys.executable, "-B", "-m", "ai_operator", command]
                if flag:
                    args.append(flag)
                result = subprocess.run(args, cwd=self.directory, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                        text=True, encoding="utf-8", capture_output=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
