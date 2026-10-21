# -*- coding: utf-8 -*-
"""[WO-QT-20261021-105] 盘后复盘入口 review_daily 测试。

承重的三条「不造数」约束（继承 combo_review，本模块不得放宽）：
  1. 后验收益缺失 → ROI 标 pending_backfill，**禁填 0**
  2. 成本预算未登记 → null，不臆造
  3. 单元不可比 → unit_mismatch，不出数
另加本模块自身语义：
  · 空态（无组合）→ 显式 empty_state，不伪造报告
  · 失败组合 → 记 errors 且**不产出 md**（给失败组合写报告就是造数）
  · compare 仅在有成功组合时生成

纪律：输出落临时目录；ledger 指向临时不存在路径（不读真实成本账）。
运行：仓根 pytest quant_backtest/tests/test_review_daily.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import review_daily as rd  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "review"
        # 指向不存在的账本：避免读真实成本账，同时覆盖 exists=False 分支
        self.ledger = Path(self.tmp.name) / "no_such_ledger.jsonl"
        self.ids = rd.load_combo_ids()          # 真实注册表（只读）
        self.assertGreaterEqual(len(self.ids), 1, "真实注册表应至少有一个组合")

    def _summary(self, **kw):
        kw.setdefault("out_root", self.out)
        kw.setdefault("ledger", self.ledger)
        return rd.run(**kw)

    def _dir(self, day):
        return self.out / day


class TestEmptyState(_Base):
    def test_no_combos_reports_empty_not_fake(self):
        """无组合 → empty_state=True、combos=0、不产生任何 md。"""
        s = self._summary(combo_ids=[], date="2026-10-21")
        self.assertTrue(s["empty_state"])
        self.assertEqual(s["combos"], 0)
        self.assertEqual(s["combo_ids"], [])
        self.assertFalse(s["compare_written"])
        files = sorted(p.name for p in self._dir("2026-10-21").iterdir())
        self.assertEqual(files, ["_summary.json"], "空态只能有 summary，不得有 md")

    def test_empty_state_summary_on_disk(self):
        self._summary(combo_ids=[], date="2026-10-21")
        d = json.loads((self._dir("2026-10-21") / "_summary.json")
                       .read_text(encoding="utf-8"))
        self.assertTrue(d["empty_state"])
        self.assertEqual(d["combos"], 0)


class TestHappyPath(_Base):
    def test_single_combo_writes_md(self):
        cid = self.ids[0]
        s = self._summary(combo_ids=[cid], date="2026-10-21")
        self.assertEqual(s["combos"], 1)
        self.assertEqual(s["errors"], [])
        self.assertTrue((self._dir("2026-10-21") / ("%s.md" % cid)).exists())
        self.assertTrue((self._dir("2026-10-21") / "_compare.md").exists())
        self.assertTrue(s["compare_written"])

    def test_all_combos(self):
        s = self._summary(combo_ids=self.ids, date="2026-10-21")
        self.assertEqual(s["combos"], len(self.ids))
        for cid in self.ids:
            self.assertTrue((self._dir("2026-10-21") / ("%s.md" % cid)).exists())

    def test_archived_by_date(self):
        cid = self.ids[0]
        self._summary(combo_ids=[cid], date="2026-10-20")
        self._summary(combo_ids=[cid], date="2026-10-21")
        self.assertTrue((self._dir("2026-10-20") / ("%s.md" % cid)).exists())
        self.assertTrue((self._dir("2026-10-21") / ("%s.md" % cid)).exists())

    def test_summary_schema_keys(self):
        s = self._summary(combo_ids=[self.ids[0]], date="2026-10-21")
        for k in ("summary_schema", "date", "generated_at", "requested",
                  "combos", "combo_ids", "errors", "roi_status_counts",
                  "compare_written", "empty_state", "cost_ledger", "out_dir",
                  "notes"):
            self.assertIn(k, s, "summary 缺字段 %s" % k)


class TestNoFabrication(_Base):
    def test_unregistered_combo_goes_to_errors(self):
        s = self._summary(combo_ids=["no_such_combo"], date="2026-10-21")
        self.assertEqual(s["combos"], 0)
        self.assertEqual(len(s["errors"]), 1)
        self.assertEqual(s["errors"][0]["combo_id"], "no_such_combo")
        self.assertTrue(s["errors"][0]["reason_code"])

    def test_failed_combo_gets_no_report_file(self):
        """核心：失败组合**不得**留下任何 md（写报告＝造数）。"""
        s = self._summary(combo_ids=["no_such_combo"], date="2026-10-21")
        self.assertFalse((self._dir("2026-10-21") / "no_such_combo.md").exists())
        self.assertFalse(s["compare_written"])
        self.assertFalse((self._dir("2026-10-21") / "_compare.md").exists())

    def test_partial_failure_keeps_good_ones(self):
        cid = self.ids[0]
        s = self._summary(combo_ids=[cid, "no_such_combo"], date="2026-10-21")
        self.assertEqual(s["combos"], 1)
        self.assertEqual(len(s["errors"]), 1)
        self.assertTrue((self._dir("2026-10-21") / ("%s.md" % cid)).exists())
        self.assertTrue(s["compare_written"])

    def test_roi_pending_not_filled_with_zero(self):
        """后验未接线 → ROI 必须 pending_backfill，且报表里 ROI 不出 0 数值。"""
        cid = self.ids[0]
        s = self._summary(combo_ids=[cid], date="2026-10-21")
        self.assertEqual(s["roi_status_counts"].get("pending_backfill"), 1)
        self.assertNotIn("computed", s["roi_status_counts"])
        md = (self._dir("2026-10-21") / ("%s.md" % cid)).read_text(encoding="utf-8")
        self.assertIn("pending_backfill", md)
        # ROI 段不得出现「- 0」这类伪造数值
        roi_sec = md.split("ROI", 1)[-1]
        self.assertNotIn("ROI：0", roi_sec)

    def test_budget_not_registered_is_null(self):
        cid = self.ids[0]
        self._summary(combo_ids=[cid], date="2026-10-21")
        md = (self._dir("2026-10-21") / ("%s.md" % cid)).read_text(encoding="utf-8")
        self.assertIn("budget_not_registered", md)


class TestCostLedgerProbe(_Base):
    def test_ledger_missing_recorded(self):
        """账本不在盘 → exists=False（区分「账本不在」与「真零成本」）。"""
        s = self._summary(combo_ids=[self.ids[0]], date="2026-10-21")
        self.assertFalse(s["cost_ledger"]["exists"])
        self.assertIn("no_such_ledger", s["cost_ledger"]["path"])

    def test_ledger_present_recorded(self):
        existed = Path(self.tmp.name) / "ledger.jsonl"
        existed.write_text("", encoding="utf-8")
        s = self._summary(combo_ids=[self.ids[0]], date="2026-10-21",
                          ledger=existed)
        self.assertTrue(s["cost_ledger"]["exists"])


class TestRegistryReadFailure(unittest.TestCase):
    def test_bad_registry_raises_not_swallowed(self):
        """注册表不可读 → 直接抛错，不得装成空态。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = Path(tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(Exception):
            rd.run(date="2026-10-21", combo_ids=None,
                   registry_path=bad, out_root=Path(tmp.name) / "out",
                   ledger=Path(tmp.name) / "x.jsonl")


if __name__ == "__main__":
    unittest.main()
