# -*- coding: utf-8 -*-
"""回归守卫：quant_backtest/backtest_engine_v2.py CLI --help 不得崩溃。

背景（WO-LEGION-AUDIT-PLAN-20261027 · BUG-01）：
  2026-10-27 审计发现 `python quant_backtest/backtest_engine_v2.py --help`
  直接 traceback：argparse 对 help 字符串做 `%` 插值，未转义的单个 `%`
  触发 `KeyError: '默认'`（slot-mode）与 `KeyError: '·'`（runup-cap）。
  修法=转义为 `%%`；本测试锁死该回归。
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "quant_backtest" / "backtest_engine_v2.py"


@pytest.mark.skipif(not SCRIPT.is_file(), reason="backtest_engine_v2.py 不存在")
def test_cli_help_exit_zero():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, "backtest_engine_v2.py --help 应 exit 0，实际 %s\nSTDERR:\n%s" % (
        r.returncode,
        (r.stderr or "")[-2000:],
    )
    assert "usage:" in (r.stdout or "").lower()
    assert "slot-mode" in (r.stdout or "")
