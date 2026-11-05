# -*- coding: utf-8 -*-
"""kg_sync_task.log 前置闸门判定单测（2026-11-05 v2）。

背景：旧口径要求"最后一行必须带 OK/exit=0"，于是任何一条非数据操作行（备份演练、
人工备注）都会永久堵死 registry 重建。2026-11-05 10:06 的 b4_restore_drill 把
09:51 那次 registry_align apply 的 exit=0 盖住，表现为"数据明明对齐却拒跑"。

v2 口径：尾部 20 行里从后往前找第一条**带结论的行**，只看它成败；无结论行跳过计数；
辅助作业（b4_* 备份演练）不参与判定，但失败要进 aux 带出去。

全部用例都在临时目录内，不读也不写生产日志。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import registry_rebuild as rr  # noqa: E402

OK_LINE = "candle_sync exit=0 2026-11-05T09:50:00+08:00"
APPLY_OK = ("registry_align date=2026-11-05T09:51:13+08:00 action=apply catalog=v40 "
            "sha=c03865c56ce42b57 physical=305 display=258 rebuild=ok exit=0")
DRILL_FAIL = ("b4_restore_drill date=2026-11-05T10:06:30+08:00 backup=2026-10-29/quant_pg_alldb.sql.gz "
              "db=quant_meta table=news.ai_reviews rows=20 md5=8214b77e53e5a35b51b938ae86b5d650 "
              "snapshot=C:\\Temp\\b4_drill_20261105 findings=P1-gzip-regression,P1-no-schedule rc=1")
FX_FILL = "fx_fill mode=recent pairs=USDCNH_FXCM.parquet dry=False 2026-11-05T08:20:07+08:00"


class KgLogGateTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "kg_sync_task.log"
        self._old = rr.KG_LOG
        rr.KG_LOG = self.path

    def tearDown(self):
        rr.KG_LOG = self._old
        self._td.cleanup()

    def _write(self, *lines):
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_missing_log_is_rejected(self):
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("日志不存在", r["reason"])

    def test_empty_log_is_rejected(self):
        self.path.write_text("", encoding="utf-8")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "日志为空")

    def test_tail_exit0_passes(self):
        self._write(OK_LINE)
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"])
        self.assertEqual(r["skipped"], 0)
        self.assertEqual(r["verdict"], OK_LINE)

    def test_tail_exit1_blocks(self):
        self._write(OK_LINE, "candle_sync exit=1 2026-11-05T10:01:00+08:00")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("未成功", r["reason"])

    def test_rc_nonzero_blocks(self):
        self._write(OK_LINE, "kg_sync WARN registry_align rc=1")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])

    def test_annotation_line_is_skipped(self):
        """旧口径在这里会失败：尾行 fx_fill 没有 OK/exit=0，但它是信息行。"""
        self._write(APPLY_OK, FX_FILL)
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["verdict"], APPLY_OK)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["aux"], [])

    def test_failed_drill_does_not_block_but_is_reported(self):
        """备份演练恢复的是快照副本，不写数据根；失败要上报，但不该锁死重建。"""
        self._write(APPLY_OK, DRILL_FAIL)
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["verdict"], APPLY_OK)
        self.assertEqual(len(r["aux"]), 1)
        self.assertIn("P1-gzip-regression", r["aux"][0])
        # last 仍是物理最后一行，供审计留痕
        self.assertEqual(r["last"], DRILL_FAIL)

    def test_failed_drill_still_reported_after_later_success(self):
        """演练之后又落了一条成功行，演练的 P1 不能被"顺便"掩盖。"""
        self._write(DRILL_FAIL, APPLY_OK)
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["verdict"], APPLY_OK)
        self.assertEqual(len(r["aux"]), 1)
        self.assertIn("P1-no-schedule", r["aux"][0])

    def test_failed_data_op_under_drill_still_blocks(self):
        """演练失败不能掩盖它前面那次真正失败的数据操作。"""
        self._write("registry_align action=apply exit=1 2026-11-05T09:00:00+08:00", DRILL_FAIL)
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("exit=1", r["reason"])
        self.assertEqual(len(r["aux"]), 1)

    def test_only_annotations_blocks(self):
        self._write(FX_FILL, "b4_restore_drill rows=20 md5=abc")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("没有可判定的数据操作结果", r["reason"])

    def test_busy_marks_block(self):
        self._write(OK_LINE, "kg_sync started 2026-11-05T10:01:00+08:00")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("进行中", r["reason"])

    def test_manual_precheck_ok_counts_as_verdict(self):
        """工具自己的 manual-precheck OK 是合法的人工解锁口径。"""
        self._write(OK_LINE,
                    "registry_align manual-precheck OK 2026-11-05T09:52:10+08:00 "
                    "by DSH-Lead (no data-root process active)")
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"])
        self.assertIn("manual-precheck", r["verdict"])

    def test_error_marker_without_exit_code_blocks(self):
        """旧口径的 bad_marks 不能因改版丢掉：无退出码的异常行仍算失败。"""
        self._write(OK_LINE, "intel_link error=timeout 2026-11-05T10:02:00+08:00")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])
        self.assertIn("未成功", r["reason"])

    def test_traceback_line_blocks(self):
        self._write(OK_LINE, "candle_sync Traceback (most recent call last):")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])

    def test_exit_code_wins_over_info_text(self):
        """行内有 OK 字样又带非零退出码时，以退出码为准。"""
        self._write("candle_sync ok-ish but exit=1 2026-11-05T10:03:00+08:00")
        r = rr.find_kg_log_state()
        self.assertFalse(r["ok"])

    def test_window_is_twenty_lines(self):
        """结论行必须在尾部 20 行内；更早的成功不再作数。"""
        filler = ["candle_sync exit=0 2026-11-05T%02d:%02d:00+08:00" % (h, m)
                  for h, m in [(9, 0), (9, 1), (9, 2), (9, 3), (9, 4), (9, 5),
                               (9, 6), (9, 7), (9, 8), (9, 9), (9, 10), (9, 11),
                               (9, 12), (9, 13), (9, 14), (9, 15), (9, 16), (9, 17),
                               (9, 18), (9, 19), (9, 20)]]
        self._write(*filler)
        r = rr.find_kg_log_state()
        self.assertTrue(r["ok"])
        self.assertEqual(r["verdict"], filler[-1])


if __name__ == "__main__":
    unittest.main()
