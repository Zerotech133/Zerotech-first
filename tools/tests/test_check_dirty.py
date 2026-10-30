# -*- coding: utf-8 -*-
"""check_dirty 纯逻辑单测（2026-10-28，P15）。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from check_dirty import ESCALATE_HOURS, KNOWN_WIP, classify, escalate_flag  # noqa: E402


def test_classify_modified():
    assert classify(" M") == "modified"
    assert classify("M ") == "modified"


def test_classify_untracked_added_deleted():
    assert classify("??") == "untracked"
    assert classify("A ") == "added"
    assert classify(" D") == "deleted"


def test_classify_renamed():
    assert classify("R ") == "renamed"


def test_known_wip_registry_covers_parallel_files():
    for p in ("portal/m_api.py", "tools/devtask.py", "quant_backtest/kg_brief.py"):
        assert p in KNOWN_WIP


def test_escalate_flag_threshold():
    """P67：>7 天长期脏文件自动升级；边界与关闭语义。"""
    assert ESCALATE_HOURS == 168.0
    assert escalate_flag(200) is True
    assert escalate_flag(169) is True
    assert escalate_flag(167) is False
    assert escalate_flag(168) is False  # 严格大于
    assert escalate_flag(None) is False
    assert escalate_flag(9999, escalate_hours=0) is False  # 0=关闭
