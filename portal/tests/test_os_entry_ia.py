# -*- coding: utf-8 -*-
"""/os-entry v10 四视图信息架构契约（静态）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "os_entry_v9_prod.html").read_text(encoding="utf-8")


def test_four_views_and_nav():
    for name in ("ai", "work", "results", "screen"):
        assert 'id="view-%s"' % name in HTML
        assert 'data-view="%s"' % name in HTML
    assert "今日焦点" in HTML
    assert "工作平台" in HTML
    assert "今日结果" in HTML
    assert "指挥大屏" in HTML


def test_kpis_and_work_search():
    for k in ("kpi-p1", "kpi-p2", "kpi-p3", "kpi-p4"):
        assert 'id="%s"' % k in HTML
    assert 'id="work-search"' in HTML
    assert 'id="suggestions"' in HTML
    assert 'id="capabilities"' in HTML
    assert 'id="source-list"' in HTML
    assert 'id="result-list"' in HTML
    assert 'id="evidence-list"' in HTML
    assert 'id="board-foundation"' in HTML


def test_row_drill_and_no_duplicate_ids():
    assert "os-row-drill" in HTML
    assert "foldSuggestions" in HTML and "os-fold" in HTML
    assert "下钻处理" in HTML
    import re
    ids = re.findall(r'id="([^"]+)"', HTML)
    assert len(ids) == len(set(ids)), "duplicate ids"


def test_keeps_evidence_contract_hooks():
    # 不重写取数逻辑：证据契约/来源投影仍存在
    assert "OSResultContract" in HTML
    assert "/static/os_zhuge_adapter.js" in HTML
    assert "/api/zhuge/brief" in HTML


def test_zhice_entry_card():
    assert "智策量化驾驶舱" in HTML
    assert "127.0.0.1:5175" in HTML
    assert "智策账户" in HTML
    assert "a.target='_blank'" in HTML
