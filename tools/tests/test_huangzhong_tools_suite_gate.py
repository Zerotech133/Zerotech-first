# -*- coding: utf-8 -*-
"""fast 门纳管：黄忠 13 个 tools 工具测试聚合执行（WO-HZ-GATE-20261031）。

背景：黄忠工具测试位于 `tools/test_huangzhong_*.py`（仓库根 tools/），不在
`tools/tests/` 目录约定内；为进 fast 门 `tools_key` 套件，本文件作为**唯一聚合入口**
用子进程跑全部黄忠工具测试（独立 pytest，避免与门禁主 pytest 的 sys.path/缓存互相污染）。

范围说明：仅收录 tools/ 下 13 个自包含（tmp 夹具、无仓库产物依赖）测试；
agent_platform/tests 的 HR/MCP 黄忠测试依赖未跟踪产物（bundle/org 等），
在独立 worktree 门禁环境不稳定，暂不纳入本聚合入口（另由 agent 侧套件覆盖）。

约定：
  · 子进程解释器 = 当前解释器（sys.executable），cwd = 仓库根；
  · 收纳清单见 FILES；新增黄忠测试时同步追加（并更新 tools/tests/test_test_gate_b1.py 计数）；
  · 任一子测试失败 → 本测试失败并在断言信息中带子进程输出尾部，便于定位。
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FILES = [
    # tools/ 根下的黄忠工具测试（20：13 基础 + P0 四项 + P1 三项）
    "tools/test_huangzhong_accept.py",
    "tools/test_huangzhong_daily.py",
    "tools/test_huangzhong_evidence_refresh.py",
    "tools/test_huangzhong_go_no_go.py",
    "tools/test_huangzhong_grade.py",
    "tools/test_huangzhong_ops.py",
    "tools/test_huangzhong_review.py",
    "tools/test_huangzhong_1104_preflight.py",
    "tools/test_huangzhong_coverage_trend.py",
    "tools/test_huangzhong_incidents.py",
    "tools/test_huangzhong_1104_receipt.py",
    "tools/test_huangzhong_drill.py",
    "tools/test_huangzhong_evidence_lint.py",
    # P0 能力增强（2026-10-31 第四轮）
    "tools/test_huangzhong_health.py",
    "tools/test_huangzhong_impact.py",
    "tools/test_huangzhong_risk_coverage.py",
    "tools/test_huangzhong_brief.py",
    # P1 能力增强（2026-10-31 第五轮）
    "tools/test_huangzhong_policy.py",
    "tools/test_huangzhong_defect_classify.py",
    "tools/test_huangzhong_cert_lifecycle.py",
]


def test_huangzhong_tool_suite_green():
    missing = [f for f in FILES if not (REPO / f).is_file()]
    assert not missing, "黄忠测试文件缺失: %s" % missing
    cmd = [sys.executable, "-X", "utf8", "-m", "pytest", *FILES,
           "-q", "--timeout=300", "-p", "no:cacheprovider"]
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=420)
    tail = ((r.stdout or "")[-4000:] + "\n" + (r.stderr or "")[-2000:])
    assert r.returncode == 0, "黄忠工具测试未全绿（rc=%s）：\n%s" % (r.returncode, tail)
