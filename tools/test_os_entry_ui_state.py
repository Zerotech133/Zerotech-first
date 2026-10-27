# -*- coding: utf-8 -*-
"""OS entry Week2 契约：tokens/状态模板/键盘入口。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "os_entry_v9_prod.html"
TOKENS = ROOT / "portal" / "static" / "tokens.css"


def test_page_uses_tokens_and_states():
    s = PAGE.read_text(encoding="utf-8")
    assert "tokens.css" in s
    assert "ui_states.css" in s
    assert "wb-skeleton" in s
    assert "wb-skip" in s
    assert 'id="view-ai" tabindex="-1"' in s
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", s), "页面不应再有硬编码 hex"


def test_os_tokens_all_defined():
    page = PAGE.read_text(encoding="utf-8")
    tokens = TOKENS.read_text(encoding="utf-8")
    used = set(re.findall(r"var\(--(os-[a-z0-9-]+)", page))
    defined = set(re.findall(r"--(os-[a-z0-9-]+):", tokens))
    assert used and not (used - defined)
