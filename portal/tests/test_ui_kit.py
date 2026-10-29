# -*- coding: utf-8 -*-
"""UI Kit v0.1 契约：组件/页面/交互原语存在。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "ui" / "ui.css").read_text(encoding="utf-8")
JS = (ROOT / "static" / "ui" / "ui.js").read_text(encoding="utf-8")
HTML = (ROOT / "ui_kit.html").read_text(encoding="utf-8")


def test_component_classes():
    for cls in (".ui-btn", ".ui-badge", ".ui-card", ".ui-panel", ".ui-kpi",
                ".ui-toolbar", ".ui-tabs", ".ui-table", ".ui-empty", ".ui-drawer",
                ".ui-toast"):
        assert cls in CSS, cls


def test_ui_js_primitives():
    assert "window.UI" in JS
    assert "function toast" in JS and "function dialog" in JS and "initTabs" in JS
    assert "data-ui-dialog" in JS and "data-ui-tabs" in JS


def test_demo_page():
    assert "零界 UI 组件库" in HTML
    for section in ("按钮 / 徽标", "KPI / 卡片", "标签页 / 表格", "工具栏 / 状态", "使用约束"):
        assert section in HTML
    assert HTML.count('id="tab-a"') == 1
    assert HTML.count('id="panel-a"') == 1
    assert HTML.count('id="tab-b"') == 1
    assert HTML.count('id="panel-b"') == 1


def test_docs_exist():
    repo = ROOT.parent
    assert (repo / "docs" / "design" / "2026-10-30_UI能力基线与开源对标_v1.md").is_file()
    assert (repo / "deliverables" / "2026-10-30_零界UI能力增强与项目重规划_v1.md").is_file()
