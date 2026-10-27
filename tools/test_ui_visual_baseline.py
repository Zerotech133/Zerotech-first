# -*- coding: utf-8 -*-
"""ui_visual_baseline 单测（不启动浏览器）。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_visual_baseline as b  # noqa: E402


def test_sha256_stable():
    assert b.sha256_bytes(b"abc") == b.sha256_bytes(b"abc")
    assert len(b.sha256_bytes(b"abc")) == 64
    assert b.sha256_bytes(b"abc") != b.sha256_bytes(b"abd")


def test_viewports_cover_desktop_mobile():
    names = [v[0] for v in b.VIEWPORTS]
    widths = {v[0]: v[1] for v in b.VIEWPORTS}
    assert names == ["desktop", "mobile"]
    assert widths["desktop"] == 1440 and widths["mobile"] == 375
