# -*- coding: utf-8 -*-
"""os_entry 建议卡 linkFor：decision_id 透传契约（静态检查）。"""
from pathlib import Path

HTML = Path(__file__).resolve().parent / "os_entry_v9_prod.html"


def _src() -> str:
    return HTML.read_text(encoding="utf-8")


def test_link_for_accepts_decision_id():
    src = _src()
    assert "decision_id=" in src
    assert "encodeURIComponent(String(x.decision_id))" in src
    assert "card_id=" in src
    # 不能因此引入新 POST/sendBeacon（C3 v3 契约）
    assert "sendBeacon" not in src


def test_brief_card_passes_decision_id():
    src = _src()
    assert "linkFor({id:s.id,decision_id:s.decision_id,card_id:s.card_id})" in src


def test_link_for_without_id_unchanged():
    src = _src()
    # 无 decision_id 时必须仍返回原领域路径分支（不改变旧行为）
    assert "return p.length?base+'?'+p.join('&'):base" in src
