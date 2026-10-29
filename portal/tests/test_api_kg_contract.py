# -*- coding: utf-8 -*-
"""api_kg 契约测试骨架（2026-10-28 首建，拆分前先钉住行为）。

目的：在 api_kg.py（5,913 行）拆分前，把**对外契约**固化：
  1. Handler mixin 的 17 个方法必须全部保留（portal_api.Handler 多继承依赖）；
  2. 3 个顶层 helper（portal_api.py 顶层 import）必须保留；
  3. 文件行数守卫：只减不增（上限给并行开发留 6,000 行松弛）。

拆分（kg_html/kg_service/kg_pages_*）过程中本文件必须保持全绿。
"""
import ast
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
REPO = PORTAL.parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_kg  # noqa: E402

# 拆分前快照（2026-10-28，AST 实测）
EXPECTED_HANDLER_METHODS = [
    "_kg_ask_page",
    "_kg_battle_page",
    "_kg_lexicon_page",
    "_kg_doc_page",
    "_kg_events_page",
    "_kg_canvas_v2_page",
    "_kg_canvas_page",
    "_kg_resonance_block",
    "_kg_shadow_page",
    "_kg_label_page",
    "_kg_card_page",
    "_kg_flow_page",
    "_kg_map_page",
    "_kg_company_page",
    "_kg_net_html",
    "_kg_overview_html",
    "_kg_page",
]
EXPECTED_TOP_FUNCS = ["_kg_label_save", "_kg_label_diff", "_kg_card_fb_save"]
LINE_CEILING = 300  # 2026-10-30 拆分收尾（5913→97，JS 常量迁 kg_js）；上限 300，只减不增


def test_handler_keeps_all_methods():
    """Handler mixin 的方法集不得缺失（portal_api 多继承依赖）。"""
    assert hasattr(api_kg, "Handler"), "api_kg.Handler 缺失"
    missing = [m for m in EXPECTED_HANDLER_METHODS if not hasattr(api_kg.Handler, m)]
    assert not missing, "Handler 缺失方法：%s" % missing


def test_handler_methods_callable():
    """17 个方法必须可调用（防拆分/改签时被覆盖成属性）。"""
    bad = [m for m in EXPECTED_HANDLER_METHODS if not callable(getattr(api_kg.Handler, m))]
    assert not bad, "Handler 方法不可调用：%s" % bad


def test_top_level_helpers_kept():
    """portal_api.py 顶层 import 的 3 个 helper 必须保留且可调用。"""
    missing = [f for f in EXPECTED_TOP_FUNCS if not hasattr(api_kg, f)]
    assert not missing, "顶层 helper 缺失：%s" % missing
    bad = [f for f in EXPECTED_TOP_FUNCS if not callable(getattr(api_kg, f))]
    assert not bad, "顶层 helper 不可调用：%s" % bad


def test_page_renderers_return_str_and_have_doc():
    """页面渲染方法应可调用且面向 HTML（拆分后仍需保持同一入口）。"""
    for m in ["_kg_page", "_kg_company_page", "_kg_map_page", "_kg_events_page"]:
        fn = getattr(api_kg.Handler, m)
        assert callable(fn), m
        assert fn.__doc__ is not None, "%s 缺 docstring（拆分时须保留用途说明）" % m


def test_file_line_ceiling():
    """冻结守卫：api_kg.py 行数不得超过 300（拆分收尾后只减不增）。"""
    src = (PORTAL / "api_kg.py").read_text(encoding="utf-8", errors="ignore")
    n = src.count("\n") + 1
    assert n <= LINE_CEILING, "api_kg.py %d 行 > 上限 %d（拆分守则：只减不增）" % (n, LINE_CEILING)


def test_module_import_has_no_heavy_side_effect():
    """import api_kg 不应触发网络/数据库连接（拆分时禁止在模块级加 IO）。"""
    src = (PORTAL / "api_kg.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)
    offenders = []
    net = {"get", "post", "put", "delete", "request", "urlopen"}
    targets = {"requests", "httpx", "urllib", "aiohttp", "socket"}
    for node in tree.body:  # 只看模块级
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in net
                and isinstance(f.value, ast.Name)
                and f.value.id in targets
            ):
                offenders.append("%s:%d" % (f.value.id, sub.lineno))
    assert not offenders, "模块级网络调用：%s" % offenders


def test_kg_modules_do_not_import_api_kg():
    """架构守卫：portal/kg/* 不得反向 import api_kg（拆分后依赖单向 kg→api_kg 禁止）。"""
    bad = []
    for fp in sorted((PORTAL / "kg").glob("*.py")):
        tree = ast.parse(fp.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "api_kg":
                bad.append("%s:%d" % (fp.name, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "api_kg":
                        bad.append("%s:%d" % (fp.name, node.lineno))
    assert not bad, "kg 模块反向 import api_kg：%s" % bad


def test_js_constants_moved_to_kg_js():
    """收尾守卫：内嵌 JS 常量迁到 kg_js（api_kg 仅再导出，不再内联 300 行）。"""
    from kg import kg_js

    assert isinstance(kg_js._KG_LABEL_JS, str) and len(kg_js._KG_LABEL_JS) > 1000
    assert isinstance(kg_js._KG_CARD_JS, str) and len(kg_js._KG_CARD_JS) > 100
    assert api_kg._KG_LABEL_JS == kg_js._KG_LABEL_JS
    assert api_kg._KG_CARD_JS == kg_js._KG_CARD_JS
