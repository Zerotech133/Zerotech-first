# -*- coding: utf-8 -*-
"""飞书推送统一封装单测（2026-10-25）。"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import m_links  # noqa: E402
import m_tokens as mt  # noqa: E402
import feishu_push as fp  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(r"C:\项目\work\_dsh_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch, ws_tmp):
    cfg = os.path.join(ws_tmp, "m_link.json")
    monkeypatch.setenv("M_LINK_CONFIG", cfg)
    monkeypatch.setattr(m_links, "CONFIG", cfg)
    monkeypatch.setenv("M_LINK_SECRET_FILE", os.path.join(ws_tmp, "s.txt"))
    monkeypatch.setattr(mt, "SECRET_FILE", os.path.join(ws_tmp, "s.txt"))
    monkeypatch.setenv("M_BASE_URL", "http://test.local:8080")
    monkeypatch.delenv("M_LINK_ENABLE", raising=False)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write('{"enabled": true, "base": "http://test.local:8080"}')
    yield cfg


def _buttons(card):
    return [e for e in card["body"]["elements"] if e.get("tag") == "button"]


def test_build_basic_layout():
    card = fp.build("daily", "统一日报", summary="今日情报 1055 条",
                    kpis=[("偏多", "17.5%"), ("高置信", "63.8%")],
                    ts="2026-10-25 16:00", source="推送中台")
    assert card["schema"] == "2.0"
    assert "📊" in card["header"]["title"]["content"]
    els = card["body"]["elements"]
    assert any("今日情报" in (e.get("content") or "") for e in els)
    assert sum(1 for e in els if e.get("tag") == "markdown") >= 3
    assert len(_buttons(card)) == 1
    assert _buttons(card)[0]["url"].startswith("http://test.local:8080/m/daily?t=")


def test_build_without_links_when_disabled(monkeypatch):
    monkeypatch.setenv("M_LINK_ENABLE", "0")
    card = fp.build("picks", "选股摘要", summary="x")
    assert _buttons(card) == []          # 绝不发死链


def test_build_stale_and_note():
    card = fp.build("ledger", "账本", summary="x", ts="2026-10-14 16:00",
                    source="账本快照", stale=True)
    text = "\n".join(e.get("content") or "" for e in card["body"]["elements"])
    assert "缓存数据" in text and "2026-10-14" in text


def test_max_kpis_and_buttons():
    card = fp.build("picks", "选股", summary="x",
                    kpis=[("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"),
                          ("e", "5")],
                    buttons=[{"type": "url", "text": "1", "url": "u1"},
                             {"type": "url", "text": "2", "url": "u2"},
                             {"type": "url", "text": "3", "url": "u3"}])
    assert len(_buttons(card)) == 2
    kpi_lines = [e for e in card["body"]["elements"]
                 if e.get("tag") == "markdown" and "　" in (e.get("content") or "")]
    assert len(kpi_lines) <= 4


def test_title_and_summary_clip():
    card = fp.build("daily", "很长的标题" * 10, summary="很长的结论" * 30)
    assert len(card["header"]["title"]["content"]) <= 30
    assert "…" in card["header"]["title"]["content"]


def test_level_colors():
    assert fp.build("alert", "预警", level="bad")["header"]["template"] == "red"
    assert fp.build("daily", "日报", level="warn")["header"]["template"] == "orange"


def test_dispatch_uses_ticket_link():
    card = fp.dispatch("lg-5917d4c4357b", "派单受理", ts="", source="派单")
    btns = _buttons(card)
    assert len(btns) == 1
    assert "/m/ticket?t=" in btns[0]["url"]
    # token 内含 id
    tok = btns[0]["url"].split("t=", 1)[1].split("&")[0]
    payload = mt.verify(tok, scope="page")
    assert payload.get("id") == "lg-5917d4c4357b"


def test_convenience_builders():
    assert "🌅" in fp.brief("x")["header"]["title"]["content"]
    assert "📊" in fp.daily("x")["header"]["title"]["content"]
    assert "🚨" in fp.alert("预警", "x")["header"]["title"]["content"]
    assert "🎯" in fp.picks_digest("x")["header"]["title"]["content"]

def test_from_sections_adapter(monkeypatch):
    from_sections = fp.from_sections
    card = from_sections("news", "notify", "金融新闻速报",
                         ["**⚡ 金融新闻速报（6 条命中）**",
                          "**JEV 批次判定** 4 利多 / 1 利空 / 1 中性",
                          "**AI 判断** 市场整体偏中性"],
                         ts="2026-10-25 16:30", source="信息采集域",
                         footer="关键词命中", links=[("📄 详情文档", "https://x")],
                         level="news")
    els = card["body"]["elements"]
    text = "\n".join(e.get("content") or "" for e in els)
    assert "金融新闻速报" in card["header"]["title"]["content"]
    assert "6 条命中" in text
    assert "JEV 批次判定" in text
    assert "https://x" in text
    assert card["header"]["template"] == "blue"     # level=news


def test_from_sections_buttons_legacy_format(monkeypatch):
    card = fp.from_sections("morning", "brief", "盘前晨报",
                            ["**总览** 三线共振", "**缠论** 60F 底分型"],
                            buttons=[("💼 交易台账", "http://x/ledger", "primary")])
    btns = _buttons(card)
    assert len(btns) == 1 and btns[0]["url"] == "http://x/ledger"


def test_should_unify_overrides(monkeypatch, ws_tmp):
    cfg = os.path.join(ws_tmp, "push_unified.json")
    monkeypatch.setattr(fp, "UNIFIED_CFG", cfg)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write('{"enabled": true, "shadow": false, "producers": {'
                '"news": {"enabled": false, "shadow": true}}}')
    assert fp.should_unify("news") == (False, True)
    assert fp.should_unify("daily") == (True, False)


def test_shadow_log_writes(monkeypatch, ws_tmp):
    path = os.path.join(ws_tmp, "data", "push_unified_shadow.jsonl")
    monkeypatch.setattr(fp, "HERE", ws_tmp)
    fp.shadow_log("news", {"header": {"title": {"content": "old"}}},
                  {"header": {"title": {"content": "new"}}})
    line = open(path, encoding="utf-8").read().strip()
    assert "news" in line and "old" in line and "new" in line
