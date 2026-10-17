#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5-P2 演示隔离安全阀测试（刀2 验收门）。

验收门：默认无 --root 跑 persist/含写侧命令=拒写退出码 2；
带 tmp root 正常；真仓账本零写副作用。
"""
import sys
import tempfile
import unittest
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "engine"))

import perf_ledger as PL  # noqa: E402


class DemoGuardTests(unittest.TestCase):
    def test_stats_without_root_rejected_exit2(self):
        # 无 --root：写侧拒绝，退出码 2（验收门原句）
        rc = PL.main(["stats"])
        self.assertEqual(rc, 2)

    def test_stats_with_tmp_root_ok(self):
        # 带 tmp root：正常执行（空账本 worst 组为空，零起草，退出 0）
        rc = PL.main(["stats", "--root", tempfile.mkdtemp(prefix="m5p2_t_")])
        self.assertEqual(rc, 0)

    def test_ls_readonly_exempt(self):
        # ls 纯只读，无 root 豁免（读真仓合法）
        rc = PL.main(["ls", "--limit", "1"])
        self.assertEqual(rc, 0)

    def test_rejected_call_leaves_real_ledger_untouched(self):
        # 拒写调用前后真仓账本零副作用
        before = [(r["perf_key"], r["declared_at"])
                  for r in PL.load_perf_rows()]
        PL.main(["stats"])  # 无 root，拒写路径
        after = [(r["perf_key"], r["declared_at"])
                 for r in PL.load_perf_rows()]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
