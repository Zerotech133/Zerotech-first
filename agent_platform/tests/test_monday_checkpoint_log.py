#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5 刀4 巡检自写日志回归测试（周一观察点证据链）。

判例来源（2026-10-17 真机实测）：计划任务 Execute 直接是 python.exe 时，
Arguments 里的 `1> log 2>&1` 不会被任何 shell 解释，只会变成脚本的普通
参数——任务照跑、退出码照出、LastRunTime 照更新，但**日志永不落盘**。
观察点门因此只剩一个整数退出码，没有任何可追溯证据。

本测试锁死"跑一次必自落一条日志"这一契约，防止该缺陷以任何形式复发。
"""
import sys
import tempfile
import unittest
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))

import monday_checkpoint as MC  # noqa: E402


class MondayCheckpointLogTests(unittest.TestCase):
    def setUp(self):
        self._old_out = sys.stdout
        self._old_err = sys.stderr
        self._old_log = MC.LOG
        self._old_handle = MC._LOG_HANDLE
        # 快照-恢复：check() 会向模块全局 RESULTS 追加条目，必须还原，
        # 否则污染后跑的测试文件（单跑绿、全量红的判例根因）。
        self._old_results = list(MC.RESULTS)
        self.tmpdir = Path(tempfile.mkdtemp(prefix="m5log_"))
        self.log = self.tmpdir / "cp.log"

    def tearDown(self):
        MC._close_log()
        MC.LOG = self._old_log
        MC._LOG_HANDLE = self._old_handle
        del MC.RESULTS[:]
        MC.RESULTS.extend(self._old_results)
        sys.stdout = self._old_out
        sys.stderr = self._old_err

    def test_tee_log_writes_file(self):
        # 自写日志：巡检输出必须同时落盘（计划任务唯一证据来源）
        MC._LOG_HANDLE = None
        MC.LOG = self.log
        MC._tee_log()
        MC.check("样例检查", True, "自写日志生效")
        sys.stdout.flush()
        MC._close_log()
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("样例检查", text)
        self.assertIn("自写日志生效", text)

    def test_tee_log_appends_not_truncates(self):
        # 追加语义：多次运行累积，供跨日追溯
        for word in ("第一次", "第二次"):
            MC._LOG_HANDLE = None
            MC.LOG = self.log
            MC._tee_log()
            print(word)
            sys.stdout.flush()
            MC._close_log()
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("第一次", text)
        self.assertIn("第二次", text)

    def test_default_log_path_contract(self):
        # 生产路径契约：计划任务/文档/脚本三处指同一个文件
        self.assertTrue(
            self._old_log.as_posix().endswith(
                "data/shadow/monday_checkpoint.log"),
            self._old_log.as_posix())

    def test_log_failure_does_not_break_patrol(self):
        # 日志不可写（父路径是普通文件）时巡检不得中断
        blocker = self.tmpdir / "blocked"
        blocker.write_text("x", encoding="utf-8")
        MC._LOG_HANDLE = None
        MC.LOG = blocker / "cp.log"
        MC._tee_log()
        self.assertIsNone(MC._LOG_HANDLE)
        print("巡检继续")  # 输出不崩即达标


if __name__ == "__main__":
    unittest.main()
