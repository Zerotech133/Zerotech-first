# -*- coding: utf-8 -*-
"""知识图谱门户数据层（kg_portal.py）单元测试。

背景：kg_portal 是 /kg（图谱探索）/kg/map（游资地图）/kg/shadow/
kg/canvasv2 等多面板的共享数据层（2199 行，与 kg_events_portal 同款
_q + api() 分发架构），此前无测试保护。参照 test_kg_events_portal.py
的三层模式补齐单元层，固化关键口径：
  · _seat_city 席位→城市解析（"路"防误配 / 区县 / 省会兜底）
  · geo 城市聚合（胜率加权、|net| 排序、未解析计数）
  · candle_coverage 分钟库文件名解析（600s TTL 缓存）
  · stats / shadow / lookup / signals 组装与参数下推
  · api() 分发路由与异常统一捕获

隔离方式：monkeypatch KP._q 拦截 Cypher；MINS_DIR 指向 tmp_path；
每用例重置 _cov_cache 防串扰。
"""
import json

import pytest

import kg_portal as KP
import kg_analytics as KA


@pytest.fixture(autouse=True)
def _reset_cov_cache():
    """每用例重置分钟库覆盖缓存（TTL 键跨用例会串）。"""
    KP._cov_cache.update({"ts": 0.0, "set": frozenset()})
    yield
    KP._cov_cache.update({"ts": 0.0, "set": frozenset()})


# ── 纯函数：日期 / 席位城市解析 ─────────────────────────────

def test_d0_format():
    from datetime import datetime, timedelta
    d = KP._d0(30)
    assert len(d) == 8 and d.isdigit()
    assert d == (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")


def test_seat_city_rules():
    sc = KP._seat_city
    assert sc("东方财富证券杭州绍兴路营业部") == "杭州"  # 城市后非"路"
    assert sc("华鑫证券上海分公司") == "上海"
    assert sc("中国银河证券北京中关村大街营业部") == "北京"
    assert sc("某某证券浦东新区营业部") == "上海"        # 区县→市
    assert sc("国泰君安浙江分公司") == "杭州"            # 省份→省会
    # "路"防误配：城市词+路（如上海绍兴路）不得误判为该城市；
    # 串内无其他地名词 → 宁可 None（计入 unresolved）
    assert sc("某某证券绍兴路营业部") is None
    # 城市词层被"路"跳过后，省份兜底仍命中（上海属 PROV_CAPITAL）
    assert sc("上海路桥集团") == "上海"
    assert sc("完全无地名") is None


# ── lookup / signals：参数下推与结构 ────────────────────────

def test_lookup_passes_q(monkeypatch):
    seen = {}

    def fake_q(cy, **kw):
        seen.update(kw)
        assert "STARTS WITH $q" in cy       # 前缀匹配在 Cypher 侧
        return [{"tc": "600000.SH", "nm": "浦发银行", "ind": "银行"}]

    monkeypatch.setattr(KP, "_q", fake_q)
    r = KP.lookup("60")
    assert seen == {"q": "60"}
    assert r["ok"] and r["q"] == "60"
    assert r["matches"][0]["tc"] == "600000.SH"


def test_signals_pushes_window(monkeypatch):
    seen = {}

    def fake_q(cy, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(KP, "_q", fake_q)
    r = KP.signals(14)
    assert len(seen["d0"]) == 8 and seen["d0"].isdigit()  # %Y%m%d 窗口
    assert r["ok"] and r["window_days"] == 14 and r["signals"] == []


# ── stats：四段 Cypher 组装（L1 注册表驱动后为全图扫描）────

def _stats_q(cy, **kw):
    if "labels(n)[0] AS lbl" in cy:                # 全量节点计数
        return [{"lbl": "Security", "c": 5911},
                {"lbl": "TradeDate", "c": 770},
                {"lbl": "Pirate", "c": 2}]         # 未注册标签（防线用）
    if "type(r) AS t" in cy:                       # 全量边计数
        return [{"t": "OF_COMPANY", "c": 5},
                {"t": "PLUNDERS", "c": 7}]         # 未注册边型
    if "max(m.date) AS me" in cy:                  # 保鲜时点
        return [{"me": "20261008", "ee": None, "te": "20261009", "cd": None}]
    if "r.hm_tag AS t" in cy:                      # 结构标签
        return [{"t": "follow_confirm", "n": 3}, {"t": "lure_warn", "n": 1}]
    raise AssertionError("unhandled: %s" % cy[:60])


def test_stats_assembles(monkeypatch):
    monkeypatch.setattr(KP, "_q", _stats_q)
    r = KP.stats()
    assert r["ok"]
    assert r["nodes"]["Security"] == 5911
    assert r["edges"]["OF_COMPANY"] == 5
    assert r["freshness"]["me"] == "20261008"
    assert r["hm_tags"] == {"follow_confirm": 3, "lure_warn": 1}


def test_stats_registry_drift_visible(monkeypatch):
    """L1：图内未注册类型必须在线可见（K264 旁路写入防线）。"""
    monkeypatch.setattr(KP, "_q", _stats_q)
    r = KP.stats()
    reg = r["registry"]
    assert reg["registered"]["nodes"] >= 30 and reg["registered"]["edges"] >= 41
    assert reg["unregistered"]["labels"] == ["Pirate"]
    assert reg["unregistered"]["rels"] == ["PLUNDERS"]


# ── shadow：三段 Cypher 组装 ────────────────────────────────

def _shadow_q(cy, **kw):
    if "post_hm10" in cy:                           # detail
        return [{"date": "20261008", "tc": "601727.SH", "nm": "上海电气",
                 "type": "大宗交易", "conf": 0.8, "qs": 1.0, "qe": 0.9,
                 "qh": 1.2, "tag": "follow_confirm", "post": 2.5}]
    if "min(m.date) AS d_min" in cy:                # total
        return [{"n": 10, "n_lead": 4, "fc": 3, "lw": 1,
                 "d_min": "20260810", "d_max": "20261008"}]
    if "sum(CASE WHEN r.hm_tag IS NOT NULL" in cy:  # byday
        return [{"date": "20261008", "n": 5, "n_tag": 2, "fc": 1, "lw": 1,
                 "n_lead": 2, "conf": 0.7, "q_hm": 1.1}]
    raise AssertionError("unhandled: %s" % cy[:60])


def test_shadow_assembles(monkeypatch):
    monkeypatch.setattr(KP, "_q", _shadow_q)
    r = KP.shadow(60)
    assert r["ok"] and r["window_days"] == 60
    assert r["detail"][0]["tag"] == "follow_confirm"
    assert r["total"]["n"] == 10 and r["total"]["d_max"] == "20261008"
    assert r["byday"][0]["fc"] == 1


# ── geo：城市聚合 / 胜率加权 / 排序 ─────────────────────────

def _geo_q(cy, **kw):
    if "RETURN st.name AS seat" in cy:              # 主聚合
        return [{"seat": "A证券杭州营业部", "n": 5, "net": 100.0,
                 "wr": 0.6, "wrn": 10},
                {"seat": "B证券上海营业部", "n": 2, "net": -50.0,
                 "wr": None, "wrn": None},
                {"seat": "神秘席位", "n": 1, "net": 10.0,
                 "wr": None, "wrn": None}]
    if "RETURN t.validStart AS d" in cy:            # 逐日矩阵
        return []
    if "AREA_OF" in cy:                             # 省份分布
        return []
    raise AssertionError("unhandled: %s" % cy[:60])


def test_geo_aggregates_cities(monkeypatch):
    monkeypatch.setattr(KP, "_q", _geo_q)
    r = KP.geo(90)
    assert r["ok"] and r["days"] == 90
    assert r["unresolved"] == 1                     # "神秘席位" 无地名
    cs = r["cities"]
    assert len(cs) == 2
    assert cs[0]["city"] == "杭州"                  # |net| 100 > 50 排序
    assert cs[0]["n_seat"] == 1 and cs[0]["n"] == 5
    assert cs[0]["wr"] == 60.0 and cs[0]["wr_disp"] == "60.0%"
    assert cs[0]["coord"] == list(KP.CITY_COORDS["杭州"])
    assert cs[1]["wr"] is None and cs[1]["wr_disp"] == "—"  # 无胜率席位
    assert r["byday"] == [] and isinstance(r["provs"], list)


# ── candle_coverage：文件名解析 + TTL 缓存 ──────────────────

def test_candle_coverage_parses_filenames(tmp_path, monkeypatch):
    for fn in ("600519_SH_202609.parquet", "000001_SZ_202609.parquet",
               "junk.txt", "noext"):
        (tmp_path / fn).write_text("x", encoding="utf-8")
    monkeypatch.setattr(KP, "MINS_DIR", str(tmp_path))
    assert KP.candle_coverage() == {"600519.SH", "000001.SZ"}
    # TTL 内缓存生效：新文件不重扫
    (tmp_path / "300750_SZ_202609.parquet").write_text("x", encoding="utf-8")
    assert KP.candle_coverage() == {"600519.SH", "000001.SZ"}


def test_candle_coverage_missing_dir(monkeypatch):
    monkeypatch.setattr(KP, "MINS_DIR", r"Z:\nope\mins")
    assert KP.candle_coverage() == frozenset()


# ── canvas：真实边、时点过滤与抽样覆盖 ─────────────────────

def test_canvas_search_company_primary_key_and_display(monkeypatch):
    def fake_q(cy, **kw):
        if "MATCH (n:Company)" in cy:
            assert "n.com_name" in cy and "n.com_id AS k" in cy
            assert kw == {"q": "同名"}
            return [{"k": "C1", "f0": "同名公司", "f1": "C1", "f2": None}]
        return []

    monkeypatch.setattr(KP, "_q", fake_q)
    hit = KP.canvas_search("同名")["hits"][0]
    assert hit["id"] == "Company|C1" and hit["name"] == "同名公司"
    assert hit["fields"]["com_id"] == "C1"


def test_canvas_expand_scoped_queries_multiedges_and_dates(monkeypatch):
    from neo4j.time import Date

    calls = []
    scope = {"types": ["OF_COMPANY"], "at": "2026-10-18"}

    def fake_q(cy, **kw):
        calls.append((cy, kw))
        if "RETURN properties(n) AS p" in cy:
            assert "toString(n.ts_code) = $k" in cy
            return [{"p": {"ts_code": "000001.SZ", "name": "标的"}}]
        if "count(*) AS c" in cy:
            return [{"t": "OF_COMPANY", "c": 2}]
        return [{"ls": ["Company"], "p": {"com_id": "C1", "com_name": "公司"},
                 "outbound": True, "rid": "r1",
                 "rp": {"source": "a", "validStart": Date(2026, 10, 17),
                        "nested": [Date(2026, 10, 18)]}},
                {"ls": ["Company"], "p": {"com_id": "C1", "com_name": "公司"},
                 "outbound": False, "rid": "r2", "rp": {"weight": 0.5}}]

    monkeypatch.setattr(KP, "_q", fake_q)
    result = KP.canvas_expand("Security|000001.SZ", scope)
    assert result["scope"] == scope
    assert result["coverage"] == {"nodes": 2, "edges": 2, "sampled": False}
    assert result["nodes"][0]["id"] == "Company|C1"
    assert [(e["id"], e["s"], e["t"]) for e in result["edges"]] == [
        ("r1", "Security|000001.SZ", "Company|C1"),
        ("r2", "Company|C1", "Security|000001.SZ")]
    assert result["edges"][0]["validStart"] == "2026-10-17"
    assert result["edges"][0]["properties"]["nested"] == ["2026-10-18"]
    json.dumps(result)
    for cy, params in calls[1:]:
        assert "type(r) IN $rel_types" in cy
        assert "r.validStart IS NOT NULL" in cy
        assert "replace(toString(r.validStart), '-', '') <= $at" in cy
        assert "replace(toString(r.validEnd), '-', '') >= $at" in cy
        assert params == {"k": "000001.SZ", "rel_types": ["OF_COMPANY"],
                          "at": "20261018"}
    assert "MATCH (n)-[r:OF_COMPANY]-(m) WHERE" in calls[2][0]


def test_canvas_expand_unscoped_and_limits(monkeypatch):
    calls = []

    def fake_q(cy, **kw):
        calls.append((cy, kw))
        if "RETURN properties(n) AS p" in cy:
            return [{"p": {"com_id": "C1", "com_name": "公司"}}]
        if "count(*) AS c" in cy:
            return [{"t": "SUPPLIES_TO", "c": 7}]
        return [{"ls": ["Company"], "p": {"com_id": "C2", "com_name": "下游"},
                 "outbound": True, "rid": str(i), "rp": {}}
                for i in range(6)]

    monkeypatch.setattr(KP, "_q", fake_q)
    result = KP.canvas_expand("Company|C1")
    assert result["coverage"] == {"nodes": 2, "edges": 6, "sampled": True}
    assert result["center"]["name"] == "公司"
    assert result["scope"] == {}
    assert " WHERE type(r)" not in calls[1][0]
    assert "MATCH (n)-[r:SUPPLIES_TO]-(m) RETURN" in calls[2][0]
    assert all(kw == {"k": "C1"} for _, kw in calls)


def test_canvas_expand_type_cap_and_unsafe_database_type(monkeypatch):
    calls = []

    def fake_q(cy, **kw):
        calls.append(cy)
        if "RETURN properties(n) AS p" in cy:
            return [{"p": {"com_id": "C1", "com_name": "公司"}}]
        if "count(*) AS c" in cy:
            return [{"t": "BAD` MATCH (x)", "c": 2}] + [
                {"t": "T%d" % i, "c": 0} for i in range(9)]
        return []

    monkeypatch.setattr(KP, "_q", fake_q)
    result = KP.canvas_expand("Company|C1")
    assert result["coverage"] == {"nodes": 1, "edges": 0, "sampled": True}
    assert len(calls) == 9  # 中心 + 分布 + 八个候选中的恶意类型被跳过
    assert all("BAD`" not in cy for cy in calls)


def test_canvas_ask_uses_edge_endpoints_not_node_zip(monkeypatch):
    import kg_digest
    import requests

    monkeypatch.setattr(kg_digest, "_ollama_ok", lambda: True)
    monkeypatch.setattr(KP, "_name_dictionary", lambda: [
        {"id": "Company|C1", "name": "主体", "label": "Company"}])
    monkeypatch.setattr(KP, "canvas_expand", lambda _id: {
        "ok": True,
        "center": {"id": "Company|C1", "name": "主体", "label": "Company"},
        "nodes": [{"id": "Segment|A", "name": "环节A", "label": "Segment"},
                  {"id": "Segment|B", "name": "环节B", "label": "Segment"}],
        "edges": [{"s": "Company|C1", "t": "Segment|A", "type": "SUPPLIES_TO"},
                  {"s": "Segment|A", "t": "Company|C1", "type": "ASSESSED"},
                  {"s": "Company|C1", "t": "Segment|B", "type": "SUPPLIES_TO"}]})

    def fake_post(url, json, timeout):
        content = json["messages"][0]["content"]
        assert "主体 -[SUPPLIES_TO]-> 环节A" in content
        assert "主体 -[ASSESSED]-> 环节A" in content
        assert "主体 -[SUPPLIES_TO]-> 环节B" in content

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "已找到"}}

        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    assert KP.canvas_ask("主体情况")["answer"] == "已找到"


def test_canvas_api_scope_forwarding(monkeypatch):
    scope = {"types": ["OF_COMPANY"], "at": "2026-10-18"}
    monkeypatch.setattr(KP, "canvas_expand", lambda arg, scope=None:
                        {"arg": arg, "scope": scope})
    assert KP.api("cexpand", "Company|C1", scope=scope) == {
        "arg": "Company|C1", "scope": scope}


def test_canvas_timeline_weekend_truncation_edges_and_dates(monkeypatch):
    from neo4j.time import Date, DateTime

    calls = []

    def fake_q(cy, **kw):
        calls.append((cy, kw))
        if "RETURN properties(s) AS p LIMIT 1" in cy:
            return [{"p": {"ts_code": "000001.SZ", "name": "平安银行"}}]
        if "(e:Event)" in cy:
            return [
                {"p": {"id": "a", "date": "20261018", "text": "周日"},
                 "links": [{"id": "forward", "type": "INVOLVES", "outbound": True,
                            "p": {"source": "feed", "validStart": Date(2026, 10, 17)}}]},
                {"p": {"id": "b", "date": Date(2026, 10, 17), "text": "周六"},
                 "links": [{"id": "reverse", "type": "INVOLVES", "outbound": False,
                            "p": {"weight": 0.5, "nested": [Date(2026, 10, 18)]}}]},
            ]
        if "(ta:TradeAction)" in cy:
            return []
        return [{"p": {"evt_id": "m", "date": "20261018",
                       "ts": DateTime(2026, 10, 18, 9, 30), "type": "limit"},
                 "links": [{"id": "market", "type": "HAS_LIMIT", "outbound": False,
                            "p": {"source": "market"}}]}]

    monkeypatch.setattr(KP, "_q", fake_q)
    result = KP.canvas_timeline("Security|000001.SZ", "2026-10-17",
                                "2026-10-18", limit=2)
    assert set(result) == {"ok", "seed", "nodes", "edges", "events", "from",
                           "to", "coverage", "days"}
    assert result["seed"]["id"] == "Security|000001.SZ"
    assert result["coverage"] == {"returned": 2, "has_more": True}
    assert result["days"] == [{"date": "2026-10-18", "count": 2}]
    assert {n["id"] for n in result["nodes"]} == {e["id"] for e in result["events"]}
    assert {e["id"] for e in result["events"]} == {"Event|a", "MarketEvent|m"}
    assert result["events"][0]["date"] == "2026-10-18"
    assert any(e["ts"].startswith("2026-10-18T09:30") for e in result["events"]
               if e["id"] == "MarketEvent|m")
    assert json.loads(json.dumps(result))["events"] == result["events"]
    assert result["from"] == "2026-10-17" and result["to"] == "2026-10-18"
    assert all(kw == {"c": "000001.SZ", "d0": "20261017", "d1": "20261018",
                      "fetch": 3} for _, kw in calls[1:])
    for cy, _ in calls[1:]:
        if "(ta:TradeAction)" in cy:
            assert "outbound:false" in cy and "TARGETS" in cy
            continue
        assert "startNode(r) = s" in cy and "-[r:" in cy and "]-(" in cy
        assert "left(replace(toString(" in cy and "ORDER BY left(replace(toString(" in cy
        assert "validStart" not in cy and "TradeDate" not in cy
    edge_by_id = {edge["id"]: edge for edge in result["edges"]}
    assert edge_by_id["forward"]["s"] == "Security|000001.SZ"
    assert edge_by_id["forward"]["validStart"] == "2026-10-17"
    assert edge_by_id["market"]["s"] == "MarketEvent|m"
    assert edge_by_id["market"]["t"] == "Security|000001.SZ"
    assert edge_by_id["market"]["properties"] == {"source": "market"}

    all_result = KP.canvas_timeline("Security|000001.SZ", "2026-10-17",
                                    "2026-10-18", limit=3)
    assert all_result["coverage"] == {"returned": 3, "has_more": False}
    assert all_result["days"] == [{"date": "2026-10-17", "count": 1},
                                  {"date": "2026-10-18", "count": 2}]
    reverse = next(e for e in all_result["edges"] if e["id"] == "reverse")
    assert (reverse["s"], reverse["t"]) == ("Event|b", "Security|000001.SZ")
    assert reverse["properties"] == {"weight": 0.5, "nested": ["2026-10-18"]}


@pytest.mark.parametrize("arg,from_date,to_date,limit", [
    ("Company|000001.SZ", None, None, None),
    ("Security|000001.SZ'", None, None, None),
    ("Security|000001.SZ", "20261017", None, None),
    ("Security|000001.SZ", "2026-02-30", None, None),
    ("Security|000001.SZ", "2026-10-18", "2026-10-17", None),
    ("Security|000001.SZ", "2025-10-17", "2026-10-18", None),
    ("Security|000001.SZ", None, None, "0"),
    ("Security|000001.SZ", None, None, "201"),
    ("Security|000001.SZ", None, None, "1.0"),
    ("Security|000001.SZ", None, None, True),
])
def test_canvas_timeline_rejects_invalid_input_before_query(monkeypatch, arg,
                                                              from_date, to_date,
                                                              limit):
    monkeypatch.setattr(KP, "_q", lambda *a, **k: pytest.fail("invalid query"))
    with pytest.raises(ValueError):
        KP.canvas_timeline(arg, from_date, to_date, limit)


def test_canvas_timeline_empty_and_missing_security(monkeypatch):
    monkeypatch.setattr(KP, "_q", lambda cy, **kw: (
        [{"p": {"ts_code": "000001.SZ"}}]
        if "RETURN properties(s) AS p LIMIT 1" in cy else []))
    r = KP.canvas_timeline("Security|000001.SZ", "2026-10-17", "2026-10-18")
    assert r["seed"]["id"] == "Security|000001.SZ" and r["events"] == []
    assert r["days"] == [] and r["coverage"] == {"returned": 0, "has_more": False}
    monkeypatch.setattr(KP, "_q", lambda *a, **k: [])
    assert KP.canvas_timeline("Security|000001.SZ")["ok"] is False


def test_canvas_timeline_http_bad_request(monkeypatch):
    from pathlib import Path
    portal_dir = str(Path(__file__).resolve().parents[1] / "portal")
    monkeypatch.syspath_prepend(portal_dir)
    import portal_api

    responses = []
    class Request:
        path = "/kg/ctimeline/Security%7C000001.SZ?from=2026-02-30&to=2026-10-18"
        def _json(self, body, status=200):
            responses.append((status, body))

    monkeypatch.setattr(KP, "_q", lambda *a, **k: pytest.fail("invalid query"))
    portal_api.Handler.do_GET(Request())
    assert responses == [(400, {"ok": False, "error": "无效日期 2026-02-30"})]


def test_canvas_timeline_frontend_contract():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "portal" / "api_kg.py").read_text(
        encoding="utf-8")
    segment = js[js.index("function clearTimeline(dropSeed)"):
                 js.index("function resetAll()", js.index("function clearTimeline(dropSeed)"))]
    assert "d.days" in segment and "d.coverage" in segment
    assert "d.seed&&d.seed.id===seed)chosen.unshift(d.seed)" in segment
    assert "mergeGraph(chosen,links,false,scope" in segment
    assert "seq!==tlSeq||scope!==scopeSeq||seed!==tlSecurity" in segment
    assert "pick!==tlPickSeq" in segment and "eventFocus:true" in segment


# ── api 分发 ───────────────────────────────────────────────

def test_api_dispatch_and_defaults(monkeypatch):
    monkeypatch.setattr(KP, "stats", lambda: {"ok": True, "tag": "stats"})
    monkeypatch.setattr(KP, "lookup", lambda q: {"ok": True, "tag": q})
    monkeypatch.setattr(KP, "signals", lambda d: {"ok": True, "wd": d})
    monkeypatch.setattr(KP, "shadow", lambda d: {"ok": True, "wd": d})
    monkeypatch.setattr(KP, "geo", lambda d: {"ok": True, "wd": d})
    assert KP.api("stats")["tag"] == "stats"
    assert KP.api("lookup", "600")["tag"] == "600"
    assert KP.api("signals")["wd"] == 14              # 各端点默认窗口
    assert KP.api("shadow")["wd"] == 60
    assert KP.api("geo")["wd"] == 90


def test_api_unknown_and_error_capture(monkeypatch):
    assert KP.api("nope")["ok"] is False
    assert "未知 kind" in KP.api("nope")["error"]

    def boom():
        raise ValueError("x")

    monkeypatch.setattr(KP, "stats", boom)
    r = KP.api("stats")                              # 异常统一捕获为 JSON
    assert r["ok"] is False and "ValueError" in r["error"]


class _AnalyticsSession:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, cy, **kw):
        self.queries.append((cy, kw))
        return _AnalyticsResult(self.rows)


class _AnalyticsResult:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        return self.rows[0] if self.rows else None


class _AnalyticsClient:
    def __init__(self, rows):
        self.session = _AnalyticsSession(rows)

    def _session(self):
        return self.session

    def close(self):
        pass


def test_analytics_resolve_uses_canvas_primary_key():
    cli = _AnalyticsClient([{"k": 42}])
    assert KA._resolve(cli, "Company|42") == {"label": "Company", "name": "42"}
    cy, params = cli.session.queries[0]
    assert "MATCH (n:Company)" in cy and "toString(n.com_id) = $n" in cy
    assert params == {"n": "42"}
    assert "toString(b.com_id) = $bn" in KA._node_match(
        {"label": "Company", "name": "42"})[1]


def test_analytics_subgraph_distinguishes_same_name(monkeypatch):
    rows = [
        {"ls": ["Security"], "sp": {"ts_code": "000001.SZ", "name": "同名"},
         "lt": ["Security"], "tp": {"ts_code": "000002.SZ", "name": "同名"},
         "sampled": True, "rid": "r1", "rtype": "OF_COMPANY", "rp": {}},
        {"ls": ["Security"], "sp": {"ts_code": "000002.SZ", "name": "同名"},
         "lt": ["Company"], "tp": {"com_id": "C1", "com_name": "同名"},
         "sampled": True, "rid": "r2", "rtype": "OF_COMPANY", "rp": {}},
    ]
    cli = _AnalyticsClient(rows)
    monkeypatch.setattr(KA, "_find_node", lambda _cli, _seed:
                        {"label": "Security", "name": "000001.SZ"})
    seed, g, err = KA._subgraph(cli, "Security|000001.SZ")
    assert err is None and seed["label"] == "Security"
    assert set(g) == {"Security|000001.SZ", "Security|000002.SZ", "Company|C1"}
    assert g.number_of_edges() == 2 and g.graph["sampled"] is True
    assert g.nodes["Company|C1"]["name"] == "同名"
    assert "properties(n) AS sp" in cli.session.queries[0][0]
    assert "elementId(r) AS rid" in cli.session.queries[0][0]
    assert "type(r) AS rtype" in cli.session.queries[0][0]


def test_analytics_community_and_centrality_keep_names(monkeypatch):
    import networkx as nx

    g = nx.path_graph(["Security|000001.SZ", "Security|000002.SZ", "Company|C1"])
    for node in g:
        g.nodes[node].update(name="同名", label=node.split("|")[0])
    g.graph["sampled"] = True
    monkeypatch.setattr(KA, "_subgraph", lambda *args:
                        ({"label": "Security", "name": "000001.SZ"}, g, None))
    monkeypatch.setattr(KA, "KgClient", lambda **kw: _AnalyticsClient([]))
    community = KA.community("Security|000001.SZ")
    assert community["n_nodes"] == 3 and community["n_edges"] == 2
    assert community["sampled"] is True
    assert set(community["communities"][0]["members"]) <= set(g)
    assert all(t["name"] == "同名" and t["id"] in g
               for c in community["communities"] for t in c["tops"])
    assert any(c["has_seed"] for c in community["communities"])
    centrality = KA.centrality("Security|000001.SZ")
    assert centrality["n_nodes"] == 3 and centrality["sampled"] is True
    for metric in ("degree", "betweenness", "pagerank"):
        assert {entry["id"] for entry in centrality[metric]} == set(g)
        assert all(entry["name"] == "同名" for entry in centrality[metric])


def test_analytics_community_returns_every_member_and_group(monkeypatch):
    import networkx as nx

    g = nx.Graph()
    groups = [{"Company|%d" % i for i in range(65)}] + [
        {"Company|%d" % i} for i in range(65, 77)]
    for group in groups:
        for node in group:
            g.add_node(node, name=node, label="Company")
        for node in sorted(group)[1:]:
            g.add_edge(min(group), node)
    g.graph["sampled"] = True
    monkeypatch.setattr(KA, "_subgraph", lambda *args:
                        ({"label": "Company", "name": "0"}, g, None))
    monkeypatch.setattr(KA, "KgClient", lambda **kw: _AnalyticsClient([]))
    monkeypatch.setattr(nx.algorithms.community, "louvain_communities",
                        lambda *args, **kwargs: groups)

    result = KA.community("Company|0")
    assert result["sampled"] is True
    assert result["n_nodes"] == 77
    assert result["n_communities"] == len(result["communities"]) == 13
    assert len(result["communities"][0]["members"]) == 65
    assert {member for group in result["communities"]
            for member in group["members"]} == set(g)


def _community_fixture(monkeypatch, edges, labels=None):
    import networkx as nx

    g = nx.Graph()
    labels = labels or {}
    for u, v, rtype, props in edges:
        for n in (u, v):
            g.add_node(n, name=n, label=labels.get(n, "Company"))
        if not g.has_edge(u, v):
            g.add_edge(u, v, relations=[])
        g[u][v]["relations"].append((rtype, props))
    g.graph["sampled"] = False
    monkeypatch.setattr(KA, "_subgraph", lambda *args:
                        ({"label": "Company", "name": "a"}, g, None))
    monkeypatch.setattr(KA, "KgClient", lambda **kw: _AnalyticsClient([]))
    return g


def test_analytics_weighted_partition_uses_relationships(monkeypatch):
    edges = [("Company|a", "Company|b", "OF_COMPANY", {"weight": 1}),
             ("Company|c", "Company|d", "OF_COMPANY", {"weight": 1}),
             ("Company|b", "Company|c", "SUPPLIES_TO", {"weight": 20}),
             ("Company|a", "Company|d", "OF_COMPANY", {"weight": 0.01})]
    g = _community_fixture(monkeypatch, edges)
    weighted = KA._community_weights(g)
    assert weighted["Company|b"]["Company|c"]["weight"] == 60
    assert weighted["Company|a"]["Company|d"]["weight"] == 0.01
    result = KA.community("Company|a")
    assert result["algorithm"] == "语义加权 Louvain + 连通性修正"
    assert result["modularity"] is not None
    assert any({"Company|b", "Company|c"} <= set(c["members"])
               for c in result["communities"])
    assert sum(c["size"] for c in result["communities"]) == len(g)


def test_analytics_event_leaf_and_hub_are_attachments(monkeypatch):
    edges = [("Company|a", "Company|b", "SUPPLIES_TO", {}),
             ("Company|c", "Company|d", "SUPPLIES_TO", {}),
             ("Company|a", "Region|shared", "AREA_OF", {}),
             ("Company|c", "Region|shared", "AREA_OF", {}),
             ("Company|b", "MarketEvent|event", "TRIGGERED", {}),
             ("MarketEvent|event", "Event|detail", "HAS_EVENT", {})]
    labels = {"Region|shared": "Region", "MarketEvent|event": "MarketEvent",
              "Event|detail": "Event"}
    g = _community_fixture(monkeypatch, edges, labels)
    result = KA.community("Company|a")
    owner = {n: c["id"] for c in result["communities"] for n in c["members"]}
    assert owner["Company|a"] == owner["Company|b"]
    assert owner["Company|c"] == owner["Company|d"]
    assert owner["Company|a"] != owner["Company|c"]
    assert owner["MarketEvent|event"] == owner["Event|detail"] == owner["Company|b"]
    assert "Region|shared" not in set().union(*(set(c["core_members"])
                                                 for c in result["communities"]))
    assert set(owner) == set(g)
    assert all(nx_is_connected(g.subgraph(c["members"])) for c in result["communities"])


def nx_is_connected(graph):
    import networkx as nx
    return nx.is_connected(graph)


def test_analytics_community_repairs_disconnected_groups(monkeypatch):
    import networkx as nx

    _community_fixture(monkeypatch, [("Company|a", "Company|b", "X", {}),
                                   ("Company|c", "Company|d", "X", {})])
    monkeypatch.setattr(nx.algorithms.community, "louvain_communities",
                        lambda *args, **kwargs: [{"Company|a", "Company|c"},
                                                 {"Company|b", "Company|d"}])
    result = KA.community("Company|a")
    assert result["n_communities"] == 4
    assert all(c["size"] == 1 for c in result["communities"])
    assert {n for c in result["communities"] for n in c["members"]} == {
        "Company|a", "Company|b", "Company|c", "Company|d"}


def test_analytics_parallel_edges_deduplicates_undirected_rows(monkeypatch):
    rows = []
    for rid, weight in (("r1", 2), ("r2", 3)):
        for reverse in (False, True):
            a, b = ("b", "a") if reverse else ("a", "b")
            rows.append({"ls": ["Company"], "sp": {"com_id": a},
                         "lt": ["Company"], "tp": {"com_id": b},
                         "rid": rid, "rtype": "SUPPLIES_TO", "rp": {"weight": weight},
                         "sampled": False})
    cli = _AnalyticsClient(rows)
    monkeypatch.setattr(KA, "_find_node", lambda *_: {"label": "Company", "name": "a"})
    _, g, _ = KA._subgraph(cli, "Company|a")
    assert g.number_of_edges() == 1
    assert g.degree("Company|a") == 1
    assert KA._community_weights(g)["Company|a"]["Company|b"]["weight"] == 15


def test_analytics_community_small_graph_metadata(monkeypatch):
    _community_fixture(monkeypatch, [("Company|a", "Company|b", "X", {})])
    result = KA.community("Company|a")
    assert result["n_communities"] == 0 and result["modularity"] is None
    assert result["algorithm"] and result["weight_basis"]


def test_analytics_path_real_edge_metadata_and_temporal(monkeypatch):
    from neo4j.time import Date

    ns = [{"ls": ["Security"], "p": {"ts_code": "000001.SZ", "name": "同名"},
           "eid": "n1"},
          {"ls": ["Company"], "p": {"com_id": "C1", "com_name": "同名"},
           "eid": "n2"}]
    rs = [{"type": "OF_COMPANY", "s": "n1", "t": "n2", "id": "rel-7",
           "p": {"source": "feed", "validStart": Date(2026, 10, 18),
                 "nested": [Date(2026, 10, 17)], "weight": 0.4}}]
    cli = _AnalyticsClient([{"ns": ns, "rs": rs, "d": 1}])
    monkeypatch.setattr(KA, "KgClient", lambda **kw: cli)
    monkeypatch.setattr(KA, "_find_node", lambda _cli, entity:
                        {"label": "Security" if entity == "a" else "Company",
                         "name": "000001.SZ" if entity == "a" else "C1"})
    result = KA.graph_path("a", "b")
    edge = result["graph"]["edges"][0]
    assert result["nodes"] == ["同名", "同名"] and result["hops"] == 1
    assert "sampled" not in result and len(result["graph"]["nodes"]) == 2
    assert len(result["graph"]["edges"]) == result["hops"]
    assert edge == {"s": "Security|000001.SZ", "t": "Company|C1",
                    "type": "OF_COMPANY", "id": "rel-7", "source": "feed",
                    "validStart": "2026-10-18", "validEnd": None, "weight": 0.4,
                    "properties": {"source": "feed", "validStart": "2026-10-18",
                                   "nested": ["2026-10-17"], "weight": 0.4}}
    assert json.loads(json.dumps(result))["graph"]["edges"][0] == edge
    cy = cli.session.queries[0][0]
    assert "elementId(r)" in cy and "properties(r)" in cy


def test_analytics_shared_neighbor_id(monkeypatch):
    cli = _AnalyticsClient([{"ls": ["Company"], "p": {"com_id": "C1"},
                             "nm": "同名", "w": 2}])
    monkeypatch.setattr(KA, "KgClient", lambda **kw: cli)
    monkeypatch.setattr(KA, "_find_node", lambda _cli, ent:
                        {"label": "Security", "name": ent})
    result = KA.shared_neighbors("a", "b")
    assert result["neighbors"][0]["id"] == "Company|C1"
    assert result["neighbors"][0]["name"] == "同名"


@pytest.mark.parametrize("scope", [
    {"types": ["AREA-OF"]}, {"types": ["area_of"]},
    {"types": ["A`B"]}, {"types": "AREA_OF"},
    {"at": "20261018"}, {"at": "2026-02-30"},
    {"at": "2026-10-18' OR true"}, {"other": 1},
])
def test_analytics_scope_validation(scope):
    with pytest.raises(ValueError):
        KA.run("path", "a", "b", scope=scope)


def test_analytics_scope_queries(monkeypatch):
    scope = {"types": ["AREA_OF", "OF_COMPANY"], "at": "2026-10-18"}
    cli = _AnalyticsClient([])
    monkeypatch.setattr(KA, "KgClient", lambda **kw: cli)
    monkeypatch.setattr(KA, "_find_node", lambda _cli, value:
                        {"label": "Security", "name": value})
    path = KA.run("path", "a", "b", scope=scope)
    assert path["scope"] == scope and path["graph"] == {"nodes": [], "edges": []}
    cy, params = cli.session.queries[-1]
    assert "shortestPath" in cy
    assert "WHERE ALL(r IN relationships(p) WHERE" in cy
    assert params == {"an": "a", "bn": "b", "rel_types": scope["types"],
                      "at": "20261018"}
    for var in ("r",):
        assert "type(%s) IN $rel_types" % var in cy
        assert "%s.validStart IS NOT NULL" % var in cy
        assert "replace(toString(%s.validStart), '-', '') <= $at" % var in cy
        assert "(%s.validEnd IS NULL OR " % var in cy
        assert "replace(toString(%s.validEnd), '-', '') >= $at" % var in cy
    shared = KA.run("shared", "a", "b", scope=scope)
    assert shared["scope"] == scope
    cy, params = cli.session.queries[-1]
    assert params["rel_types"] == scope["types"] and params["at"] == "20261018"
    for var in ("r1", "r2"):
        assert "type(%s) IN $rel_types" % var in cy
        assert "%s.validStart IS NOT NULL" % var in cy
        assert "replace(toString(%s.validEnd), '-', '') >= $at" % var in cy
    seed, g, err = KA._subgraph(cli, "a", scope=scope)
    assert err is None and g.graph["scope"] == scope and g.graph["sampled"] is False
    cy, params = cli.session.queries[-1]
    assert params == {"an": "a", "rel_types": scope["types"], "at": "20261018"}
    assert "OPTIONAL MATCH p = (a)-[*1..2]-(x)" in cy
    assert "ALL(edge IN relationships(p) WHERE type(edge) IN $rel_types" in cy
    assert "edge.validStart IS NOT NULL" in cy
    assert "m IN all AND type(r) IN $rel_types" in cy
    assert "r.validStart IS NOT NULL" in cy
    assert KA.run("community", "a", scope=scope)["scope"] == scope
    assert KA.run("centrality", "a", scope=scope)["scope"] == scope


def test_analytics_types_only_and_unscoped(monkeypatch):
    cli = _AnalyticsClient([])
    monkeypatch.setattr(KA, "KgClient", lambda **kw: cli)
    monkeypatch.setattr(KA, "_find_node", lambda _cli, value:
                        {"label": "Security", "name": value})
    assert KA.graph_path("a", "b")["scope"] == {}
    cy, params = cli.session.queries[-1]
    assert "WHERE ALL" not in cy and params == {"an": "a", "bn": "b"}
    assert KA.graph_path("a", "b", scope={"types": []})["scope"] == {"types": []}
    cy, params = cli.session.queries[-1]
    assert "type(r) IN $rel_types" in cy and params["rel_types"] == []
