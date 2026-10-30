# -*- coding: utf-8 -*-
"""devtask 评审门注册测试（P74：CIGATES 三道门恢复）。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402


def test_cigates_gates_registered():
    names = [n for n, _ in devtask.GATES]
    assert "语法门" in names
    assert "导入边界" in names
    assert "元数据一致性" in names
    # 恢复的三门位于评审门序列末尾（不改变既有门顺序）
    assert names[-3:] == ["语法门", "导入边界", "元数据一致性"]


def test_metadata_known_warn_only_contains_registry_sha():
    assert "registry_catalog_sha_mismatch" in devtask.KNOWN_META_WARN_ONLY


def test_gate_callables_have_expected_signature():
    for name in ("_syntax_gate", "_import_boundary_gate", "_metadata_gate"):
        fn = getattr(devtask, name)
        assert callable(fn), name
