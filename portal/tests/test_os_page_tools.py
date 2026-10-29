# -*- coding: utf-8 -*-
"""OS 页面工具（搜索/折叠）契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
CSS = (ROOT / "static" / "os_page_tools.css").read_text(encoding="utf-8")
JS = (ROOT / "static" / "os_page_tools.js").read_text(encoding="utf-8")
TARGETS = (
    "os.html", "quant.html", "quant_drill.html", "quant_paper.html",
    "quant_v2_demo.html", "intel_workbench.html", "news_intel.html",
    "xq_intel.html", "intel_dossier_skeleton.html",
)


def test_assets_have_features():
    assert ".os-page-tools" in CSS and ".os-tool-collapsed" in CSS
    assert "normalizeBoard" in JS and "os-tool-block" in JS
    assert "折叠全部分区" in JS and "os-tools-count" in JS


def test_targets_injected_once():
    for name in TARGETS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert text.count("os_page_tools.css") == 1, name
        assert text.count("os_page_tools.js") == 1, name
