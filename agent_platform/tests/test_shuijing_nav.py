# -*- coding: utf-8 -*-
"""司马徽页导航可发现性（2026-11-03 WO-ACAD1-NAV2-20261103）。

独立测试文件：不改共享 ``test_shuijing_ui.py``（避免与 ACAD1-DARK 分支冲突）。
覆盖：
1) nav.js（由 tools/export_nav.py 从 portal/api_common.py 重导）含
   「🧭 司马徽研究台」/「/academy/shuijing」入口；
2) api_common.py 源含同一入口，且序列化形态与 nav.js 一致（防源/导出漂移）；
3) page_shell.js MODERN 判定命中该路径（走新版页壳，不注入旧版回退条）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAV = ROOT / "portal" / "static" / "nav.js"
COMMON = ROOT / "portal" / "api_common.py"
SHELL = ROOT / "portal" / "static" / "page_shell.js"

ENTRY_PATH = "/academy/shuijing"
ENTRY_NAME = "司马徽研究台"
# export_nav.py 的序列化形态：["🧭 司马徽研究台", "/academy/shuijing", "shuijing"]
ENTRY_JS = '["🧭 司马徽研究台", "/academy/shuijing", "shuijing"]'
ENTRY_PY = '("🧭 司马徽研究台", "/academy/shuijing", "shuijing")'


def test_nav_js_contains_entry():
    """nav.js 含路径+名称+键（export_nav 重导产物，不手写）。"""
    nav = NAV.read_text(encoding="utf-8")
    assert ENTRY_PATH in nav
    assert ENTRY_NAME in nav
    assert ENTRY_JS in nav


def test_api_common_source_has_entry():
    """api_common.py dev 域「🎓 零界学院」分组含入口源。"""
    common = COMMON.read_text(encoding="utf-8")
    assert ENTRY_PATH in common
    assert ENTRY_NAME in common
    assert ENTRY_PY in common


def test_nav_js_matches_api_common_source():
    """源与导出一致：同一入口序列化形态同时存在于 api_common.py 与 nav.js。"""
    common = COMMON.read_text(encoding="utf-8")
    nav = NAV.read_text(encoding="utf-8")
    assert ENTRY_PY in common
    assert ENTRY_JS in nav


def test_page_shell_modern_contains_path():
    """page_shell.js MODERN 命中 /academy/shuijing（新版页壳，旧版回退条不注入）。"""
    shell = SHELL.read_text(encoding="utf-8")
    assert "'/academy/shuijing': 1" in shell
