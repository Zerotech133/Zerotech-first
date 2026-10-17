# -*- coding: utf-8 -*-
"""M0 P5 业务域投影适配器测试（离线 fixture）。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ba = _load("business_adapter_under_test", "agent_platform/adapters/business_adapter.py")


class BusinessAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel: str, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_missing_sources_reported_read_only(self):
        report = ba.build_business_report(self.root)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["projects"]["source_status"]["registry.projects"],
                         "missing_source")
        self.assertEqual(report["projects"]["source_status"]["portal.projects_registry"],
                         "missing_source")
        self.assertEqual(report["academy"]["status"], "missing_source")
        self.assertEqual(report["gzh"]["status"], "missing_source")

    def test_project_dual_source_zero_overlap(self):
        self._write("agent_platform/registry/projects.json", {
            "projects": [{"project_id": "quant", "name": "量化双刀",
                          "dept": "dev", "k_prefix": "K-STOCK",
                          "members": ["DEV1"], "charter": "c", "status": "active"}]})
        self._write("portal/projects_registry.json", {
            "version": 1, "projects": [
                {"id": "proj_data", "name": "数据底层治理", "dept_label": "B",
                 "phase": "dev", "progress": 65, "lead": "DATA1", "summary": "s",
                 "members": [{"id": "DATA1", "grade": "E2", "role": "r"}],
                 "tasks": [{"t": "a", "owner": "DATA1", "st": "done"},
                           {"t": "b", "owner": "DATA1", "st": "doing"}]}]})
        report = ba.build_business_report(self.root)
        proj = report["projects"]
        self.assertEqual(proj["registry_count"], 1)
        self.assertEqual(proj["portal_count"], 1)
        self.assertEqual(proj["matched_ids"], [])
        # portal 记录保留 phase 原样与子任务统计
        portal_rec = next(r for r in proj["records"]
                          if r["source"] == "portal.projects_registry")
        self.assertEqual(portal_rec["status"], "dev")
        self.assertEqual(portal_rec["subtask_status"], {"todo": 0, "doing": 1, "done": 1})

    def test_project_matched_ids_reported_not_merged(self):
        self._write("agent_platform/registry/projects.json", {
            "projects": [{"project_id": "quant", "name": "q", "status": "active"}]})
        self._write("portal/projects_registry.json", {
            "projects": [{"id": "quant", "name": "q-ui", "phase": "test"}]})
        report = ba.build_business_report(self.root)
        proj = report["projects"]
        self.assertEqual(proj["matched_ids"], ["quant"])
        # 两源记录都保留，不合并成一条
        self.assertEqual(len([r for r in proj["records"] if r["project_id"] == "quant"]), 2)

    def test_academy_stage_preserved_not_remapped(self):
        self._write("agent_platform/data/academy/research_topics.json", [
            {"topic_id": "AC-1", "title": "T", "owner": "STRAT1",
             "direction": "strategy_lab", "status": "intake",
             "history": [{"x": 1}, {"x": 2}]}])
        report = ba.build_business_report(self.root)
        rec = report["academy"]["records"][0]
        self.assertEqual(rec["stage"], "intake")          # 阶段机原样
        self.assertEqual(rec["stage_index"], 0)
        self.assertEqual(rec["history_len"], 2)

    def test_gzh_md_existence_checked(self):
        self._write("gzh/drafts/index.json", [
            {"id": "d1", "date": "2026-10-17", "title": "A", "col": "c",
             "status": "draft", "md": "a.md"},
            {"id": "d2", "date": "2026-10-17", "title": "B", "col": "c",
             "status": "published", "md": "missing.md"}])
        (self.root / "gzh" / "drafts" / "a.md").write_text("x", encoding="utf-8")
        report = ba.build_business_report(self.root)
        gzh = report["gzh"]
        self.assertEqual(gzh["count"], 2)
        self.assertEqual(gzh["missing_md_count"], 1)
        self.assertEqual(gzh["by_status"], {"draft": 1, "published": 1})
        rec_missing = next(r for r in gzh["records"] if r["draft_id"] == "d2")
        self.assertFalse(rec_missing["md_exists"])

    def test_corrupt_json_reported_not_crash(self):
        (self.root / "gzh" / "drafts").mkdir(parents=True)
        (self.root / "gzh" / "drafts" / "index.json").write_text(
            "{not json", encoding="utf-8")
        report = ba.build_business_report(self.root)
        self.assertEqual(report["gzh"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
