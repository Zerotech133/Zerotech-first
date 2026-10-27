# -*- coding: utf-8 -*-
"""双版本 UI 契约：浅色驾驶舱 6 导航/亮对话 + 深色深屏互链。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIGHT = ROOT / "portal" / "zero_proto.html"
DARK = ROOT / "portal" / "os_entry_v9_prod.html"


def test_light_cockpit_six_nav_and_hero():
    s = LIGHT.read_text(encoding="utf-8")
    expected = ["首页", "会议指挥", "情报全谱", "业务域", "产品建设", "系统"]
    for label in expected:
        assert '>%s</button>' % label in s, label
    assert "对零界说一句话——我来指挥整个系统" in s
    assert "零界 AI 驾驶舱 v0.2 · 浅色" in s
    assert "/static/nav.js" not in s.split("<body>")[1]


def test_light_footer_links_dark():
    s = LIGHT.read_text(encoding="utf-8")
    assert 'href="/os-entry"' in s
    assert "v9 深屏" in s


def test_dark_footer_links_light():
    s = DARK.read_text(encoding="utf-8")
    assert "← 返回 AI 驾驶舱" in s
    assert 'href="/zero_proto.html"' in s
