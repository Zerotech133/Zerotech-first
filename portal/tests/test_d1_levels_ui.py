# -*- coding: utf-8 -*-
"""D1 水平来源/ATR/支撑阻力展示与 pending 防误导回归（WO-D1-LEVEL-UI-20261031）。

覆盖：
1) 静态页：详情含“水平来源 / ATR / 支撑 / 阻力”字段，保留 tokens/nav/shortscreen 三件套；
2) 展示契约（mock，不依赖真实行情）：19 只中 technical_atr_support_resistance
   只落在已研究 5 只；pending 14 只标“未研究”、levels 为空、不展示技术推导水平；
3) pending 即使拿到旧规则回退数值，展示层仍不得标记为技术推导（防误导）；
4) portal 8091 在线时校验真实数据同一契约（不可达则 skip，不依赖真实行情）。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent.parent
REPO = PORTAL.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PORTAL))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

DONE_CODES = ["002531", "001286", "001388", "002112", "002712"]
PENDING_CODES = ["002840", "002841", "003007", "300017", "300251", "600011",
                 "600022", "603088", "603596", "603992", "688196", "688282",
                 "920454", "920885"]
TECH_SRC = "technical_atr_support_resistance"
PAGE = PORTAL / "d1_research.html"


def _load_d1_data():
    spec = importlib.util.spec_from_file_location("d1_data_levelui_t", PORTAL / "d1_data.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_page_has_level_source_fields():
    html = PAGE.read_text(encoding="utf-8")
    for token in ("水平来源", "ATR", "支撑", "阻力", "technical_atr_support_resistance",
                  "level_source", "level_source_label", "level_show_derived",
                  "level_pending", "未研究", "旧规则", "/api/d1/research"):
        assert token in html, "页面缺字段/口径：%s" % token
    # 前端准入三件套与表头不得被重写
    for token in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js",
                  "止损", "止盈", "交棒"):
        assert token in html, token


def test_page_pending_guard_logic():
    html = PAGE.read_text(encoding="utf-8")
    # pending 的买点/止损/止盈走“未研究”占位，而不是直接渲染数值
    for token in ("function isPending(", "function levelVal(", "function targetVal(",
                  "function srcLabel(", "level_source_label"):
        assert token in html, token
    assert "isPending(x)?'<span class=\"lv legacy\">未研究</span>'" in html


def test_level_view_contract():
    mod = _load_d1_data()
    tech = {"level_source": TECH_SRC, "levels": {"support": 9.0, "resistance": 10.0}}
    v = mod._level_view(tech, pending=False)
    assert v["level_show_derived"] is True
    assert v["level_pending"] is False
    assert "技术推导" in v["level_source_label"]
    # 已研究票若被判定 pending，同样不得展示技术推导水平
    v = mod._level_view(tech, pending=True)
    assert v["level_show_derived"] is False
    assert "未研究" in v["level_source_label"]
    v = mod._level_view({"level_source": "legacy_rule", "levels": {}}, pending=False)
    assert v["level_show_derived"] is False
    assert "旧规则" in v["level_source_label"]
    v = mod._level_view({"levels": {}}, pending=True)
    assert v["level_show_derived"] is False
    assert "未研究" in v["level_source_label"]


def _mock_snapshot(monkeypatch):
    """19 只 mock 池：5 已研究（done）+ 14 pending；不依赖真实行情/数据文件。"""
    mod = _load_d1_data()
    items = [{"code": code, "name": "样本%d" % i, "tier": "T1",
              "industry": "test", "sources": ["acad1"], "data_date": "2026-10-30"}
             for i, code in enumerate(DONE_CODES + PENDING_CODES)]
    # 故意给一只 pending 票价格：旧规则回退会给出数值，展示层仍必须隐藏
    items[-1]["price"] = 9.9
    records = [{"code": code, "decision_status": "done", "recommendation": "WATCH",
                "confidence": 0.6, "price": 10.0 + i,
                "analyses": {"T1": "多", "T2": "空"}}
               for i, code in enumerate(DONE_CODES)]
    review = {"schema": "d1-pool-review-test", "records": records,
              "plan": {"rows": []}, "summary": {}}
    derive_calls = []

    def fake_derive(code, price=None, can_buy=False, use_cache=True):
        derive_calls.append({"code": code, "price": price, "can_buy": can_buy})
        return {"watch_trigger": {"pullback": round(price * 0.99, 2),
                                  "breakout": round(price * 1.01, 2)},
                "stop_loss": round(price * 0.9, 2),
                "target1": round(price * 1.1, 2),
                "target2": round(price * 1.15, 2),
                "atr": 0.31, "ma20": round(price * 0.98, 2),
                "support": round(price * 0.93, 2),
                "resistance": round(price * 1.06, 2),
                "source": TECH_SRC}

    monkeypatch.setattr(mod.pool_data, "pool_snapshot",
                        lambda day=None: {"date": "2026-10-30", "pool_id": "p-d1-levelui",
                                          "data_date": "2026-10-30", "freshness_days": 0,
                                          "stale": False, "dates": ["2026-10-30"],
                                          "items": items})

    def fake_read(path, default=None):
        if str(path).endswith("2026-10-30.json"):
            return review
        return {} if default is None else default

    monkeypatch.setattr(mod, "_read", fake_read)
    monkeypatch.setattr(mod, "_shadow_latest", lambda day: {})
    monkeypatch.setattr(mod.technical_levels, "derive", fake_derive)
    return mod, derive_calls


def test_snapshot_mock_19_5_14_and_technical_only_done(monkeypatch):
    mod, derive_calls = _mock_snapshot(monkeypatch)
    snap = mod.d1_snapshot("2026-10-30")
    assert snap["ok"] is True and snap["mode"] == "d1_research"
    items = snap["items"]
    assert len(items) == 19
    summary = snap["summary"]
    assert (summary["total"], summary["analyzed"], summary["pending_research"]) == (19, 5, 14)

    tech = [x for x in items if x["level_show_derived"]]
    assert len(tech) == 5, "技术推导只能给已研究 5 只"
    for x in tech:
        assert x["level_source"] == TECH_SRC
        assert x["decision_status"] == "done"
        assert x["level_pending"] is False
        assert "技术推导" in x["level_source_label"]
        assert (x.get("levels") or {}).get("support") is not None
        assert (x.get("levels") or {}).get("resistance") is not None
        assert x["level_note"]

    # pending 不推导：derive 只被调用 5 次且全部是已研究票
    assert len(derive_calls) == 5
    assert {c["code"] for c in derive_calls} == set(DONE_CODES)

    pending = [x for x in items if x["level_pending"]]
    assert len(pending) == 14
    for x in pending:
        assert x["level_show_derived"] is False
        assert "未研究" in x["level_source_label"]
        assert x["levels"] == {}
        assert x["decision_status"] != "done"


def test_pending_with_legacy_fallback_not_shown_as_technical(monkeypatch):
    mod, _ = _mock_snapshot(monkeypatch)
    snap = mod.d1_snapshot("2026-10-30")
    pending_with_price = [x for x in snap["items"]
                         if x["level_pending"] and x.get("price")]
    assert pending_with_price, "构造用例应含带价 pending 票"
    for x in pending_with_price:
        # 旧规则回退可能给出止损/止盈数值，但展示契约必须禁止标成技术推导
        assert x["stop_loss"] is not None
        assert x["level_show_derived"] is False
        assert "未研究" in x["level_source_label"]
        assert x["level_source"] != TECH_SRC


def test_live_api_level_contract_or_skip():
    """portal 8091 在线时校验真实数据同一契约；不可达则 skip（不依赖真实行情）。"""
    try:
        with safe_urlopen("http://127.0.0.1:8091/api/d1/research",
                                    timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 端口未起/超时/非 JSON → 跳过，不阻断离线门
        pytest.skip("portal 8091 不可达，跳过在线契约校验")
    if not data.get("ok"):
        pytest.skip("接口未就绪")
    items = data.get("items") or []
    for x in items:
        if x.get("decision_status") != "done":
            assert x.get("level_source") != TECH_SRC
            assert not (x.get("levels") or {})
    tech = [x for x in items if x.get("level_source") == TECH_SRC]
    assert all(x.get("decision_status") == "done" for x in tech)
