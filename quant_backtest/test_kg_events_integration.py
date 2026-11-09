# -*- coding: utf-8 -*-
"""K266 事件图谱查询面板集成测试（真实链路）。

与 test_kg_events_portal.py（单元层：mock _q + tmp 文件隔离）互补，
本文件验证真实组件拼装出的行为：
  A. 数据层直连：真实 Neo4j（KgClient 单例）+ data/k266_docs 真实
     影子/原文文件——situ 守恒口径、过滤下推、下钻 doc_id 关联。
  B. HTTP 层：运行中的 portal（127.0.0.1:8091）——页面占位符替换
     完整性（漏一个即白屏）、/kg/events/* JSON 路由、query 参数
     parse_qs 列表→标量归一、market=全部 不下推字面量（线上回归）。

环境约定：
  · Neo4j / portal 未运行时对应组自动 skip（集成测试声明依赖，
    不制造环境）；
  · K266 每 5 分钟一拍摄取、数据持续增长 → 全部用结构性断言，
    不锁具体行数；公司/市场样本从全量结果动态取样。
运行：cd quant_backtest && python -m pytest test_kg_events_integration.py -v
"""

import json
import urllib.parse
from datetime import datetime

import pytest

import kg_events_portal as KE

PORTAL = "http://127.0.0.1:8091"
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402


# ── 环境探测（不可达即 skip）────────────────────────────────


@pytest.fixture(scope="module")
def live():
    """真实 Neo4j + 真实影子文件的数据层前置。"""
    s = KE.situ()
    if not (s.get("ok") and s.get("neo4j_up")):
        pytest.skip("Neo4j 不可用：%s" % s.get("error", "unknown"))
    return s


@pytest.fixture(scope="module")
def http():
    """运行中的 portal 前置（打最轻的 situ 端点探测）。"""
    try:
        with safe_urlopen(PORTAL + "/kg/events/situ", timeout=5) as r:
            assert json.loads(r.read().decode("utf-8")).get("ok")
    except Exception as ex:  # 探测阶段任何失败都退化为 skip
        pytest.skip("portal(8091) 不可用：%s" % ex)
    return PORTAL


def _get(path, timeout=15):
    with safe_urlopen(PORTAL + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def _get_json(path, **params):
    if params:
        path += "?" + urllib.parse.urlencode(params)
    st, body = _get(path)
    return st, json.loads(body)


# ── A. 数据层直连（真实 Neo4j + 真实文件）───────────────────


def test_situ_live_consistency(live):
    s = live
    assert s["doc_edges"] >= 1 and s["sec_involved"] >= 1
    datetime.strptime(s["latest_event_ts"], "%Y-%m-%d %H:%M:%S")
    # 市场分解按端点去重求和应与 sec_involved 守恒（含未分类桶）
    assert sum(s["inv_markets"].values()) == s["sec_involved"]
    assert s["rels"] and s["dirs"]
    assert s["struct"]["count"] >= 1 and s["texts"]["count"] >= 1
    assert s["event_shadow"]["count"] >= 1
    assert s["cadence_min"] == KE.INGEST_CADENCE_MIN


def _full_graph():
    g = KE.graph({})
    assert g["ok"] and g["total"] >= 1
    return g


def test_graph_live_shape(live):
    g = _full_graph()
    ids = [n["id"] for n in g["nodes"]]
    assert len(set(ids)) == len(ids)  # 节点 id 唯一
    for n in g["nodes"]:
        assert n["data"]["deg"] >= 1 and n["data"]["market"]  # 已归一非空
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids  # 边端点闭合
        assert e["id"].count(">>") == 1 and e["id"].endswith(
            "%s|%s" % (e["data"]["rel"], e["data"]["dir"])
        )
        assert e["data"]["n"] >= 1
        assert e["data"]["src_doc"].startswith("d")  # 条级 doc_id


def test_graph_live_market_filter(live):
    full = _full_graph()
    mk = full["nodes"][0]["data"]["market"]  # 动态取样真实市场
    g = KE.graph({"market": mk})
    assert g["ok"] and 1 <= g["total"] <= full["total"]
    mkt = {n["id"]: n["data"]["market"] for n in g["nodes"]}
    for e in g["edges"]:  # OR 语义：至少一端命中
        assert mk in (mkt[e["source"]], mkt[e["target"]])


def test_graph_live_query_filter(live):
    full = _full_graph()
    e0 = full["edges"][0]
    nm = next(n["data"]["name"] for n in full["nodes"] if n["id"] == e0["source"])
    g = KE.graph({"q": nm})
    assert g["ok"] and 1 <= g["total"] <= full["total"]


def test_list_live_company_drilldown(live):
    code = _full_graph()["edges"][0]["source"]
    r = KE.list_rows({"c": code})
    assert r["ok"] and r["total"] >= 1
    assert all(
        row["ac"] == code or row["bc"] == code for row in r["rows"]
    )  # 双端任一命中即返回


def test_doc_live_item_then_document_level(live):
    """页面下钻主链路：条级 src_doc → 原文；同 id 去后缀文档级再查等价。"""
    r = KE.list_rows({})
    assert r["total"] >= 1
    src = r["rows"][0]["src_doc"]
    d = KE.doc(src)
    assert d["ok"] and d["doc_level"] == KE._doc_level(src)
    assert d["found_text"] or d["found_struct"]
    if d["found_text"]:
        assert d["text"] and d["text_chars"] >= 1
    d2 = KE.doc(d["doc_level"])  # 原文恒按文档级键
    assert d2["ok"] and d2["found_text"] == d["found_text"]


def test_search_live_hit_order_and_empty(live):
    g = _full_graph()
    e0 = g["edges"][0]
    code = e0["source"]
    nm = next(n["data"]["name"] for n in g["nodes"] if n["id"] == code)
    s = KE.search({"q": nm})
    assert s["ok"]
    hit = next(h for h in s["hits"] if h["code"] == code)
    assert hit["n_events"] >= 1  # 从图上取的公司必有事件
    ns = [h["n_events"] for h in s["hits"]]
    assert ns == sorted(ns, reverse=True)  # 事件数降序联想
    assert KE.search({"q": "  "}) == {"ok": True, "hits": []}


# ── B. HTTP 层（运行中的 portal 8091）───────────────────────


def test_http_page_placeholders(http):
    st, html = _get("/kg/events")
    assert st == 200 and "事件图谱" in html
    # 8 个占位符漏替换任何一个 → 页面白屏/断链（__INIT__ 在 JS 里）
    for token in (
        "__CSS__",
        "__NAV__",
        "__SITU__",
        "__TABS__",
        "__FORM__",
        "__GSTAT__",
        "__LEGEND__",
        "__INIT__",
    ):
        assert token not in html, "占位符未替换：%s" % token
    assert "const INIT=" in html  # INIT 数据已内联注入
    assert "g6" in html.lower()  # 本地 G6 静态引用


def test_http_situ_and_graph_all_tab(http):
    st, s = _get_json("/kg/events/situ")
    assert st == 200 and s["ok"] and s["neo4j_up"]
    # market=全部：页面默认 Tab，经 parse_qs 归一后不得下推为字面量
    st, g = _get_json("/kg/events/graph", market="全部")
    assert st == 200 and g["ok"] and g["edges"]


def test_http_drilldown_list_doc_search(http):
    st, r = _get_json("/kg/events/list")
    assert st == 200 and r["ok"] and r["total"] >= 1
    row = r["rows"][0]
    st, d = _get_json("/kg/events/doc/" + row["src_doc"])
    assert st == 200 and d["ok"] and (d["found_text"] or d["found_struct"])
    st, s = _get_json("/kg/events/search", q=row["an"])  # 中文 q 走编码
    assert st == 200 and s["ok"]
    assert any(h["name"] == row["an"] for h in s["hits"])
