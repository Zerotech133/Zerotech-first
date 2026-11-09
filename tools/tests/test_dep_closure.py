# -*- coding: utf-8 -*-
"""依赖闭包检查器的测试（R12）。

守卫的核心是 `test_current_tree_is_closed`：只要有人新增了「已入库但 import
未入库模块」的代码，这条测试就红。它对应 2026-11-06 的真实事故 ——
`api_trace_drilldown` 已入库却 import 了从未入库的 `kg_cq`，导致新 clone 起不来。
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "check_dep_closure.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_dep_closure", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def test_tool_exists():
    assert TOOL.is_file(), "依赖闭包检查器缺失"


def test_current_tree_is_closed(tool):
    """当前已入库代码必须依赖闭合——这是本次事故的回归守卫。

    若红：把报出的 target 一并入库，或给 import 加 try/except 并明示降级。
    不要靠改这条测试来变绿。
    """
    res = tool.scan(("portal", "quant_backtest"))
    assert res["checked_files"] > 100, "扫描文件数异常少，可能路径判断出错"
    assert res["broken"] == [], (
        "存在「已入库 → 未入库」引用，新 clone 会 ModuleNotFoundError：\n  "
        + "\n  ".join("%s → %s" % (b["importer"], b["module"]) for b in res["broken"])
    )


def test_local_modules_detects_only_local(tool):
    """只把仓库内真实存在的模块算作本地模块，标准库与三方包不算。"""
    sample = REPO / "quant_backtest" / "kg_cq.py"
    mods = tool._local_modules(sample, REPO)
    assert "cq_registry" in mods, "漏掉真实本地依赖"
    assert "ontology" in mods, "漏掉真实本地依赖"
    # 标准库不得出现
    assert "os" not in mods and "json" not in mods and "sys" not in mods
    # 三方包（仓库里没有同名 py）不得出现
    assert "pandas" not in mods and "numpy" not in mods


def test_resolve_prefers_existing_file(tool):
    p = tool._resolve("kg_cq", REPO)
    assert p is not None and p.exists()


def test_detector_catches_synthetic_breakage(tool, monkeypatch):
    """反向验证：把某个 target 判成未入库后，检查器必须报出断裂。

    没有这条，"当前树闭合"可能只是因为检查器什么都不报（假绿）。

    注意 mock 方向：要让 **importer 仍算已入库**、只让 target 失效。
    若把 _tracked 整体打成 False，scan 会因为「未入库文件引用的东西不管」
    把所有文件都 continue 掉，得到空结果——那是测试写错，不是检查器失效。
    """
    monkeypatch.setattr(tool, "_tracked",
                        lambda p: "cq_registry" not in str(p).replace("\\", "/"))
    res = tool.scan(("quant_backtest",))
    assert res["checked_files"] > 0, "importer 全被跳过，mock 方向错了"
    assert res["broken"], "检查器无法发现断裂，是假绿"
    assert any(b["module"] == "cq_registry" for b in res["broken"])


def test_main_exit_code_reflects_breakage(tool, monkeypatch):
    monkeypatch.setattr(tool, "_tracked", lambda p: True)
    assert tool.main(["quant_backtest"]) == 0
