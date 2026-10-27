# -*- coding: utf-8 -*-
"""zero_proto → Quickstart 一句话入口结构测试。"""
import os

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_zero_proto_has_quick_entry():
    text = open(os.path.join(PORTAL, "zero_proto.html"), encoding="utf-8").read()
    assert 'id="os-quick-entry"' in text
    assert "/os_quick.html?text=1" in text
    assert "prefill=" in text


def test_quick_supports_prefill():
    text = open(os.path.join(PORTAL, "os_quick.html"), encoding="utf-8").read()
    assert "PREFILL" in text
    assert "qs.get('prefill')" in text
    assert "ask').value=PREFILL" in text
