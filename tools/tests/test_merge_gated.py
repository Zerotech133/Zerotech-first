# -*- coding: utf-8 -*-
"""merge_gated 纯逻辑单测（2026-10-28，P7）。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from merge_gated import DOC_PREFIX, DOC_SUFFIX, is_docs_only  # noqa: E402


def test_docs_only_true_for_docs_files():
    assert is_docs_only(["docs/plans/x.md", "deliverables/y.md"])
    assert is_docs_only(["agent_platform/skills/guanyu/SKILL.md"])


def test_docs_only_true_for_frontend_assets():
    # A9（2026-10-31）：html/css 属纯静态资产可跳过；.js 是前端代码，不得免测
    assert is_docs_only(["portal/static/tokens.css", "portal/static/index.html"])
    assert not is_docs_only(["portal/static/nav.js"])
    assert not is_docs_only(["portal/static/tokens.css", "portal/static/nav.js"])


def test_docs_only_false_for_py():
    assert not is_docs_only(["tools/devtask.py"])
    assert not is_docs_only(["docs/a.md", "portal/api_kg.py"])


def test_docs_only_false_for_root_md():
    # 根目录文档不在白名单前缀内 → 视为代码变更，需跑测试（保守）
    assert not is_docs_only(["README.md"])


def test_docs_only_false_for_md_outside_prefix():
    # 非白名单前缀（即使带子目录）也不能跳过测试，否则质量闸可被绕过
    assert not is_docs_only(["quant_backtest/notes.md"])
    assert not is_docs_only(["agent_platform/notes.json"])
    assert not is_docs_only(["tools/ops.md"])


def test_docs_only_false_for_empty():
    assert not is_docs_only([])


def test_suffix_and_prefix_sets_sane():
    assert ".md" in DOC_SUFFIX
    assert "docs/" in DOC_PREFIX

def test_docs_only_handles_quoted_non_ascii_paths():
    """git core.quotepath 未关时中文路径会带引号；_clean_path 兜底后仍应识别。"""
    from merge_gated import _clean_path, is_docs_only
    assert _clean_path('"docs/ops/2026-10-28_中文.md"') == "docs/ops/2026-10-28_中文.md"
    assert is_docs_only([_clean_path('"docs/ops/2026-10-28_中文.md"')])
