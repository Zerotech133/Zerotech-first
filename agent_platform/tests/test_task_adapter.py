# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from adapters.task_adapter import build_report, main  # noqa: E402


class TaskAdapterTest(unittest.TestCase):
    def test_read_only_mapping_and_review_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = root / "agent_platform/data/org/tasks"
            task_dir.mkdir(parents=True)
            canonical = {
                "task_id": "T-1",
                "status": "doing",
                "title": "规范任务",
                "assigned_agent": "DEV1",
            }
            event = {"event": "task_start", "task_id": "T-1", "status": "done"}
            (task_dir / "tasks_202610.jsonl").write_text(
                json.dumps(canonical) + "\n" + json.dumps(event) + "\n",
                encoding="utf-8",
            )
            legacy_path = root / ".worktrees"
            legacy_path.mkdir()
            legacy = {
                "T-1": {"status": "review", "desc": "旧研发任务", "owner": "DEV1"},
                "L-2": {"status": "done", "desc": "已完成旧任务", "owner": "DEV2"},
            }
            (legacy_path / "tasks.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            before = (
                (task_dir / "tasks_202610.jsonl").read_bytes(),
                (legacy_path / "tasks.json").read_bytes(),
            )
            report = build_report(root)
            self.assertTrue(report["read_only"])
            self.assertEqual(report["coverage"]["matched_task_ids"], 1)
            review = next(
                x
                for x in report["records"]
                if x["source"] == "devtask_registry" and x["task_id"] == "T-1"
            )
            self.assertIsNone(review["canonical_status"])
            self.assertEqual(review["mapping_confidence"], 0.35)
            self.assertEqual(review["normalized_status"], "review")
            self.assertTrue(
                any("禁止自动升级" in c["reason"] for c in review["conflicts"])
            )
            self.assertEqual(report["sources"]["canonical"]["line_count"], 1)
            self.assertEqual(
                before,
                (
                    (task_dir / "tasks_202610.jsonl").read_bytes(),
                    (legacy_path / "tasks.json").read_bytes(),
                ),
            )

    def test_missing_sources_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            report = build_report(temp)
            self.assertEqual(report["sources"]["canonical"]["exists"], False)
            self.assertEqual(report["sources"]["legacy"]["exists"], False)
            self.assertEqual(
                {x["kind"] for x in report["source_errors"]}, {"missing_source"}
            )
            self.assertEqual(report["snapshot_freshness"]["legacy"]["state"], "missing")

    def test_cli_writes_json_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "reports/tasks.json"
            self.assertEqual(main(["--root", str(root), "--output", str(out)]), 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["adapter_version"], "m0-task-readonly-v1")
            self.assertTrue(payload["read_only"])


if __name__ == "__main__":
    unittest.main()
