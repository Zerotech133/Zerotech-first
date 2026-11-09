# -*- coding: utf-8 -*-
"""知识图谱门户（kg_portal.py 共享数据层）集成测试（真实链路）。

与 test_kg_portal.py（单元层）互补，走真实组件：
  A. 数据层直连：真实 Neo4j 打 KP.api() 只读端点（stats/lookup/
     signals/shadow/geo）——节点计数、联想前缀、影子窗口结构。
  B. HTTP 层：运行中的 portal（8091）——/kg/stats /kg/sources
     JSON 端点与 /kg /kg/map /kg/canvasv2 页面渲染。

环境约定（同 test_kg_events_integration.py）：
  · Neo4j / portal 未运行时对应组自动 skip；
  · 数据持续增长 → 结构性断言不锁具体计数。
运行：cd quant_backtest && python -m pytest test_kg_portal_integration.py -v
"""

import json

import pytest

import kg_portal as KP

PORTAL = "http://127.0.0.1:8091"
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402


# ── 环境探测（不可达即 skip）────────────────────────────────


@pytest.fixture(scope="module")
def live():
    s = KP.api("stats")
    if not s.get("ok"):
        pytest.skip("Neo4j 不可用：%s" % s.get("error", "unknown"))
    return s


@pytest.fixture(scope="module")
def http():
    try:
        with safe_urlopen(PORTAL + "/kg/stats", timeout=5) as r:
            assert json.loads(r.read().decode("utf-8")).get("ok")
    except Exception as ex:
        pytest.skip("portal(8091) 不可用：%s" % ex)
    return PORTAL


def _get(path, timeout=30):
    with safe_urlopen(PORTAL + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


# ── A. 数据层直连（真实 Neo4j）─────────────────────────────


def test_stats_live(live):
    r = live
    assert r["nodes"]["Security"] >= 5000  # K266 入图主节点池
    assert all(isinstance(v, int) for v in r["edges"].values())
    assert r["edges"] and sum(r["edges"].values()) > 0
    assert "freshness" in r and "hm_tags" in r


def test_lookup_live_prefix(live):
    r = KP.api("lookup", "600")
    assert r["ok"] and 1 <= len(r["matches"]) <= 8
    assert any(m["tc"].startswith("600") for m in r["matches"])
    for m in r["matches"]:  # 返回字段契约
        assert "tc" in m and "nm" in m and "ind" in m


def test_signals_live_structure(live):
    r = KP.api("signals", None, 14)
    assert r["ok"] and r["window_days"] == 14
    assert isinstance(r["signals"], list) and len(r["signals"]) <= 50
    for s in r["signals"]:  # 行字段契约
        assert s["tag"] in ("follow_confirm", "lure_warn")
        assert len(s["date"]) == 8


def test_shadow_live_structure(live):
    r = KP.api("shadow", None, 60)
    assert r["ok"] and r["window_days"] == 60
    t = r["total"]
    assert {"n", "n_lead", "fc", "lw", "d_min", "d_max"} <= set(t)
    assert len(r["byday"]) == len({b["date"] for b in r["byday"]})  # 日期唯一


def test_geo_live_structure(live):
    r = KP.api("geo", None, 90)
    assert r["ok"] and r["days"] == 90
    assert isinstance(r["cities"], list) and isinstance(r["provs"], list)
    nets = [abs(c["net"]) for c in r["cities"]]  # |net| 降序
    assert nets == sorted(nets, reverse=True)
    for c in r["cities"]:  # 命中城市必有坐标
        assert c["city"] in KP.CITY_COORDS and len(c["coord"]) == 2


# ── B. HTTP 层（运行中的 portal 8091）───────────────────────


def test_http_json_endpoints(http):
    st, body = _get("/kg/stats")
    assert st == 200 and json.loads(body)["ok"]
    st, body = _get("/kg/sources")  # 结构或 ok 键必居其一
    d = json.loads(body)
    assert st == 200 and ("ok" in d or "sources" in d)


def test_ontology_live_snapshot(live):
    """L1 注册表×图内实况：全量计数 + 保鲜 + 零漂移（旁路写入防线）。"""
    r = KP.api("ontology")
    assert r["ok"], r.get("error")
    sec = r["node_types"]["Security"]
    assert sec["count"] >= 5000 and sec["key"] == "ts_code"
    assert sec["trust"] and sec["writer"]  # 元数据四件套在线
    d = r["drift"]
    assert d["unregistered_labels"] == []  # 图内类型必须全注册
    assert d["unregistered_rels"] == []
    assert r["graph_counts"]["nodes_total"] > 1_000_000
    # 保鲜锚：MarketEvent 有 date 字段且非空（防前视锚点可用）
    assert r["freshness"]["MarketEvent"]


def test_http_ontology_endpoint(http):
    st, body = _get("/kg/ontology")
    d = json.loads(body)
    assert st == 200 and d.get("ok")
    assert d["node_types"]["Security"]["count"] > 0


def test_objectview_live(live):
    """L2 扇区体检：真图装配，徽章字段齐全、_stock 已剥。"""
    r = KP.api("objectview", "002743.SZ", 120)
    assert r["ok"], r.get("error")
    assert "_stock" not in r
    ids = {s["id"] for s in r["sectors"]}
    assert {"basic", "structures", "hotmoney", "holders", "similar"} <= ids
    for s in r["sectors"]:
        assert s["grade"] in ("ok", "stale", "empty", "static")
        assert s["trust"] in ("A", "B", "C", "D", "E", "F") or s["id"] == "basic"
        if s["grade"] in ("ok", "stale"):
            assert s["fresh"] and len(s["fresh"]) == 8  # 有锚才有档


def test_http_objectview_endpoint(http):
    st, body = _get("/kg/objectview/002743.SZ")
    d = json.loads(body)
    assert st == 200 and d.get("ok")
    assert any(s["id"] == "holders" for s in d["sectors"])


def test_http_stock_page_has_objectview(http):
    """门户个股页含 L2 扇区体检条（徽章条渲染指纹）。"""
    st, html = _get("/kg?c=002743.SZ", timeout=60)
    assert st == 200
    assert "Object View · 扇区体检" in html
    assert "滞后" in html and "触发式" in html


def test_http_pages_render(http):
    for path, mark in (
        ("/kg", "图谱探索"),
        ("/kg/map", "游资地图"),
        ("/kg/canvasv2", "知识画布"),
    ):
        st, html = _get(path, timeout=60)  # 服务端渲染可能较慢
        assert st == 200 and mark in html and len(html) > 1000


def test_http_canvasv2_js_escape(http):
    """回归（JS 转义层级）：canvasv2 gxGo 路径渲染的 onclick 属性曾以
    Python \\" 送达裸引号，提前闭合 JS 双引号字符串 → 整个 script 块
    SyntaxError（Unexpected identifier 'document'），页面交互全失效。
    正确形态：送达 JS 内必须是转义引号 onclick=\\"…（含收尾 \\\">"）。"""
    st, html = _get("/kg/canvasv2")
    assert st == 200
    assert r"onclick=\"document" in html  # 开引号已转义（修复指纹）
    assert r"(new Event('submit'))\">" in html  # 收尾引号已转义
    assert 'onclick="document' not in html  # 裸引号（坏形态）不得再现
