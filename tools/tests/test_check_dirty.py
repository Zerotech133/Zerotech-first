# -*- coding: utf-8 -*-
"""check_dirty 纯逻辑单测（2026-10-28，P15）。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from check_dirty import KNOWN_WIP, classify  # noqa: E402


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
