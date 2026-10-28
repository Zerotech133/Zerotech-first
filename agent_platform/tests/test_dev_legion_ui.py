# -*- coding: utf-8 -*-
"""LS-08 前端框架测试：三件套/接口/区块/无自绘色值。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "portal" / "dev_legion.html"
TXT = HTML.read_text(encoding="utf-8")


def test_three_piece():
    assert "/static/tokens.css" in TXT
    assert "/static/nav.js" in TXT


def test_fetch_and_sections():
    assert 'fetch("/api/dev/legion"' in TXT
    for sid in ("groups", "seats", "wip", "metrics", "redlines"):
        assert ("id=\"" + sid + "\"") in TXT
    for sec in ("组织架构", "在制任务", "C6 指标", "红线状态"):
        assert sec in TXT


def test_shortscreen_rule():
    # h2 < 6，无需 shortscreen.js；若未来加到 >=6 必须引入
    assert len(re.findall(r"<h2[ >]", TXT)) < 6


def test_no_hardcoded_hex_colors():
    # tokens 单源：页面样式不得自绘 hex 色（#fff/#123456 之类）
    style = TXT.split("<style>", 1)[1].split("</style>", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,8}", style), "禁止自绘 hex 色"
