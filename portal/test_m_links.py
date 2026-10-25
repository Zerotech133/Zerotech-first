# -*- coding: utf-8 -*-
"""m_links 卡片链接自动签发单测（P1 后续批次）。"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import m_links  # noqa: E402
import m_tokens as mt  # noqa: E402


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
    monkeypatch.setenv("M_LINK_SECRET_FILE", os.path.join(ws_tmp, "secret.txt"))
    monkeypatch.setattr(mt, "SECRET_FILE", os.path.join(ws_tmp, "secret.txt"))
    monkeypatch.setenv("M_BASE_URL", "http://test.local:8080")
    cfg = os.path.join(ws_tmp, "m_link.json")
    monkeypatch.setenv("M_LINK_CONFIG", cfg)
    monkeypatch.setattr(m_links, "CONFIG", cfg)
    monkeypatch.delenv("M_LINK_ENABLE", raising=False)
    yield cfg


def _write_cfg(path, d):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def test_default_disabled(_env):
    assert m_links.enabled() is False
    assert m_links.url_for("picks") == ""
    assert m_links.url_for("picks", force=True).startswith("http://test.local:8080/m/picks?t=")


def test_enabled_by_config(_env, monkeypatch):
    monkeypatch.delenv("M_BASE_URL", raising=False)
    _write_cfg(_env, {"enabled": True, "base": "https://m.example.com"})
    assert m_links.enabled() is True
    url = m_links.url_for("chan", code="605100")
    assert url.startswith("https://m.example.com/m/chan?t=")
    tok = url.split("t=", 1)[1].split("&")[0]
    p = mt.verify(tok, scope="page")
    assert p["scene"] == "chan" and p["code"] == "605100"


def test_env_enable_overrides_config(_env, monkeypatch):
    _write_cfg(_env, {"enabled": False})
    monkeypatch.setenv("M_LINK_ENABLE", "1")
    assert m_links.enabled() is True
    monkeypatch.setenv("M_LINK_ENABLE", "0")
    assert m_links.enabled() is False


def test_signing_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("M_LINK_ENABLE", "1")
    import m_api
    monkeypatch.setattr(m_api, "page_url",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert m_links.url_for("picks") == ""


def test_url_for_with_st(_env):
    _write_cfg(_env, {"enabled": True, "base": "http://test.local:8080"})
    u = m_links.url_for("picks", st="st.abc.def")
    assert u.startswith("http://test.local:8080/m/picks?t=")
    assert u.endswith("&st=st.abc.def")


def test_drill_card_button_gated(monkeypatch):
    _write_cfg(m_links.CONFIG, {"enabled": False, "base": ""})
    import feishu_card_v2 as fc
    card = fc.build_drill_card("605100", {"name": "华丰", "quote": {}},
                               [("结构", "x")])
    els = card["body"]["elements"]
    labels = [e.get("text", {}).get("content") for e in els
              if e.get("tag") == "button"]
    assert "移动端详情" not in labels


def test_drill_card_button_when_enabled(monkeypatch):
    import feishu_card_v2 as fc
    _write_cfg(m_links.CONFIG, {"enabled": True, "base": "http://test.local:8080"})
    card = fc.build_drill_card("605100", {"name": "华丰", "quote": {}},
                               [("结构", "x")])
    els = card["body"]["elements"]
    mbtns = [e for e in els if e.get("tag") == "button"
             and e.get("text", {}).get("content") == "移动端详情"]
    assert len(mbtns) == 1
    u = mbtns[0]["behaviors"][0]["default_url"]
    assert "/m/chan?t=" in u
