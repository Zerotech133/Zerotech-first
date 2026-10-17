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


class EvalShadowGuardTests(unittest.TestCase):
    """M5-P2 补口：eval_shadow persist 段安全阀（污染源本体——并行会话 62cca0e
    只阀了 perf_ledger CLI stats，eval_shadow --limit 演示跑仍可落真仓）。"""

    def test_argparse_default_deny(self):
        # --persist-root 缺省空串 = main 侧拒写分支前提（subprocess 验 --help）
        import subprocess
        r = subprocess.run(
            [sys.executable, str(AP / "eval_shadow.py"), "--help"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertIn("--persist-root", r.stdout)

    def test_persist_eval_rows_with_tmp_root(self):
        # 显式 tmp root：正常落账（perf_ledger + trace_links 链挂接）
        import eval_shadow as ES
        root = Path(tempfile.mkdtemp(prefix="m5p2es_"))
        row = {
            "date": "2026-10-16", "symbol": "600519", "run_id": "run-es-1",
            "score": 3.2, "confidence": 0.7, "ret_pct": 1.25, "horizon": 3,
            "base": 1500.0, "exit": 1518.75, "recommendation": "buy",
            "stance": "坚守", "dk_regime": "rotation", "dk_profile": "relaxed",
            "dk_blocked": False,
        }
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rep, drafted, df = ES.persist_eval_rows([row], root)
        self.assertEqual(rep["recorded"], 1)
        self.assertTrue(PL.load_perf_rows(root))  # perf 落账（root=AP 根→data/）
        # 链必须落 data/trace/（真仓约定：persist_root/"data"/"trace"/trace_links.jsonl；
        # 曾错拼 persist_root/"trace" 致链落 agent_platform/trace/ 错位目录，幂等救场无污染）
        self.assertTrue((root / "data" / "trace" / "trace_links.jsonl").exists())
        self.assertFalse((root / "trace" / "trace_links.jsonl").exists())

    def test_persist_eval_rows_rejects_empty_root(self):
        # 防呆：persist_eval_rows 本体不接受空 root（拒写判定在 main，双保险）
        import eval_shadow as ES
        with self.assertRaises(Exception):
            ES.persist_eval_rows([], None)


if __name__ == "__main__":
    unittest.main()
