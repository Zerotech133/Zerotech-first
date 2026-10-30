# -*- coding: utf-8 -*-
"""17:10 自动钩子导入修复测试（2026-10-30 WO-INV-AUTO-FIX）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "agent_platform"))


def test_load_repo_tool_does_not_shadow_engine_tools():
    import run_shadow
    import tools as engine_tools
    paper = run_shadow._load_repo_tool("paper_daily")
    pool = run_shadow._load_repo_tool("pool_review_daily")
    assert hasattr(paper, "run")
    assert hasattr(pool, "review")
    # 关键：engine/tools.py 仍在，未被 repo tools 包名遮蔽
    assert hasattr(engine_tools, "make_context")
