# -*- coding: utf-8 -*-
"""merge_preflight 纯逻辑单测（2026-10-28，P13）。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from merge_preflight import is_docs_only  # noqa: E402


def test_docs_only_true():
    assert is_docs_only(["docs/plans/x.md", "deliverables/y.md"])
    assert is_docs_only(["agent_platform/skills/guanyu/SKILL.md"])
    assert is_docs_only(["portal/static/nav.js"])


def test_docs_only_false_for_code():
    assert not is_docs_only(["tools/merge_preflight.py"])
    assert not is_docs_only(["docs/a.md", "portal/api_kg.py"])


def test_docs_only_false_for_empty_or_root_md():
    assert not is_docs_only([])
    assert not is_docs_only(["README.md"])


def test_docs_only_false_for_mixed():
    assert not is_docs_only(["docs/a.md", "tools/x.json", "tools/y.py"])
