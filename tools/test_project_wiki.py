# -*- coding: utf-8 -*-
"""project_wiki 生成器契约。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "tools" / "project_wiki.py"


def _load():
    spec = importlib.util.spec_from_file_location("_project_wiki_test", str(MOD))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_adr_rows_parse_title(tmp_path):
    mod = _load()
    d = tmp_path / "adr"
    d.mkdir()
    (d / "ADR-001-test.md").write_text("# ADR-001 测试标题\n\n正文\n", encoding="utf-8")
    rows = mod._adr_rows(d)
    assert rows == [("ADR-001-test.md", "ADR-001 测试标题")]


def test_render_has_required_sections():
    mod = _load()
    text = mod.render()
    assert "# 零界项目 Wiki" in text
    assert "## 1. 仓库地图" in text
    assert "## 3. ADR 索引" in text
    assert "## 4. 约定与入口" in text
    assert "AGENTS.md" in text


def test_generated_wiki_is_current():
    mod = _load()
    out = Path(mod.DEFAULT_OUT)
    assert out.is_file(), "docs/project_wiki.md 未生成"
    assert out.read_text(encoding="utf-8").replace("\r\n", "\n") == mod.render(), "wiki 过期，重跑 tools/project_wiki.py"
