# -*- coding: utf-8 -*-
"""K266 事件图谱查询面板（kg_events_portal.py 数据层）单元测试。

背景：/kg/events 面板 2026-10-09 上线时无测试保护。本文件固化关键口径：
  · 过滤归一：market=“全部”/空串 → None（曾出过 INIT 0 边的线上 bug）
  · 路由分发索引、doc 关联键（条级 iNN 后缀剥离）等已由端点验证，
    此处保护纯逻辑与降级行为，不依赖运行中的 Neo4j / portal。

隔离方式：monkeypatch KE._q 拦截全部 Cypher；影子/原文文件指向
pytest tmp_path；每用例重置模块级缓存防串扰。
"""
import json

import pytest

import kg_events_portal as KE


@pytest.fixture(autouse=True)
def _reset_caches():
    """每用例重置文件缓存（mtime 键跨用例会串）。"""
    KE._txt_cache.update({"mtime": -1.0, "map": {}})
    KE._struct_cache.update({"mtime": -1.0, "map": {}})
    yield
    KE._txt_cache.update({"mtime": -1.0, "map": {}})
    KE._struct_cache.update({"mtime": -1.0, "map": {}})


# ── 纯函数：过滤归一 / doc_id 关联键 ────────────────────────

def test_fx_normalizes_empty():
    assert KE._fx(None) is None
    assert KE._fx("") is None
    assert KE._fx("   ") is None
    assert KE._fx("主板") == "主板"


def test_fm_market_all_means_no_filter():
    """页面默认 Tab“全部”不得下推为字面量过滤（线上 bug 回归锚）。"""
    assert KE._fm("全部") is None
    assert KE._fm("") is None
    assert KE._fm("  主板  ") == "主板"
    assert KE._fm(None) is None


def test_doc_level_strips_inn_suffix():
    assert KE._doc_level("d19b9f5a6fcfai07") == "d19b9f5a6fcfa"
    assert KE._doc_level("d4949acc459b0i31") == "d4949acc459b0"
    assert KE._doc_level("d4949acc459b0") == "d4949acc459b0"  # 文档级原样
    assert KE._doc_level("") == ""
    assert KE._doc_level(None) == ""
    assert KE._doc_level("di07") == "d"      # 短 id + iNN 仍剥
    assert KE._doc_level("abc123") == "abc123"  # 结尾非 iNN 不动


# ── graph：节点/边组装（mock _q）────────────────────────────

def _edge(an="上海电气", ac="601727.SH", am="主板",
          bn="上海电气", bc="601727.SH", bm="主板",
          rel="其他", d="中性", n=1, date="", amount="",
          src="d19b9f5a6fcfai07", ts="2026-10-08 15:56:46"):
    return {"an": an, "ac": ac, "am": am, "bn": bn, "bc": bc, "bm": bm,
            "rel": rel, "dir": d, "n": n, "date": date, "amount": amount,
            "src_doc": src, "ts_last": ts}


def test_graph_assembles_nodes_edges_deg(monkeypatch):
    rows = [
        _edge(an="东吴证券", ac="601555.SH", bn="华锡有色", bc="600301.SH",
              rel="投资", d="利好", n=3, date="2026-10-08",
              amount="20亿元", src="db86c33cd4551i25"),
        _edge(rel="增持", d="利好", n=2),
    ]
    monkeypatch.setattr(KE, "_q", lambda cy, **kw: rows)
    g = KE.graph({})
    assert g["ok"] and g["total"] == 2
    assert len(g["nodes"]) == 3                       # 自环两端同节点，去重
    deg = {n["id"]: n["data"]["deg"] for n in g["nodes"]}
    assert deg == {"601555.SH": 1, "600301.SH": 1,
                   "601727.SH": 2}                     # 自环 deg=2
    e0 = g["edges"][0]
    assert e0["id"] == "601555.SH>>600301.SH:投资|利好"  # 边 id 规范
    assert e0["data"]["n"] == 3 and e0["data"]["src_doc"] == "db86c33cd4551i25"
    assert g["nodes"][0]["data"]["market"] == "主板"


def test_graph_passes_filters_and_normalizes_market(monkeypatch):
    seen = {}

    def fake_q(cy, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(KE, "_q", fake_q)
    KE.graph({"market": "全部", "q": "电气", "dir": "利好", "rel": "投资",
              "d0": "2026-10-01", "d1": "2026-10-09"})
    assert seen["m"] is None                    # “全部”→不过滤
    assert seen["q"] == "电气" and seen["dir"] == "利好"
    assert seen["rel"] == "投资"
    assert seen["d0"] == "2026-10-01" and seen["d1"] == "2026-10-09"


def test_graph_defaults_and_empty(monkeypatch):
    seen = {}

    def fake_q(cy, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(KE, "_q", fake_q)
    g = KE.graph(None)                            # None qp 容错
    assert g["ok"] and g["nodes"] == [] and g["edges"] == []
    assert seen["m"] is None and seen["q"] is None


def test_graph_neo4j_down(monkeypatch):
    def boom(cy, **kw):
        raise ConnectionError("bolt refused")

    monkeypatch.setattr(KE, "_q", boom)
    g = KE.graph({})
    assert g["ok"] is False and "bolt refused" in g["error"]


def test_graph_n_default_1(monkeypatch):
    monkeypatch.setattr(KE, "_q", lambda cy, **kw: [_edge(n=None)])
    g = KE.graph({})
    assert g["edges"][0]["data"]["n"] == 1        # NULL n → 1


# ── list / search（mock _q）─────────────────────────────────

def test_list_passes_code_filter(monkeypatch):
    seen = {}

    def fake_q(cy, **kw):
        seen.update(kw)
        return [_edge()]

    monkeypatch.setattr(KE, "_q", fake_q)
    r = KE.list_rows({"c": "601727.SH", "market": "全部", "q": " "})
    assert r["ok"] and r["total"] == 1
    assert seen["c"] == "601727.SH"
    assert seen["m"] is None and seen["q"] is None  # 空/全部归一


def test_list_neo4j_down(monkeypatch):
    monkeypatch.setattr(KE, "_q", lambda cy, **kw: (_ for _ in ()).throw(
        RuntimeError("down")))
    r = KE.list_rows({})
    assert r["ok"] is False and "down" in r["error"]


def test_search_empty_q_no_db_call(monkeypatch):
    called = []

    def fake_q(cy, **kw):
        called.append(cy)
        return []

    monkeypatch.setattr(KE, "_q", fake_q)
    r = KE.search({"q": "  "})
    assert r == {"ok": True, "hits": []} and called == []


def test_search_maps_hits(monkeypatch):
    monkeypatch.setattr(KE, "_q", lambda cy, **kw: [
        {"name": "上海电气", "code": "601727.SH", "market": "主板",
         "n_events": 1},
        {"name": "ST旭电(退)", "code": "000413.SZ", "market": None,
         "n_events": 0}])
    r = KE.search({"q": "上海"})
    assert r["hits"][0] == {"name": "上海电气", "code": "601727.SH",
                            "market": "主板", "n_events": 1}
    assert r["hits"][1]["market"] == "未分类"      # NULL market 降级


# ── situ：字段级降级（mock _q + tmp 文件）───────────────────

def _situ_q(cy, **kw):
    """按 situ() 内五段 Cypher 分发固定数据。

    注意顺序：markets 段也含 "count(DISTINCT s) AS n" 子串，
    必须先判 coalesce(s.market 再落到 sec_involved 分支。
    """
    if "max(r.ts_last)" in cy:
        return [{"n": 14, "latest": "2026-10-09 00:56:59"}]
    if "coalesce(s.market" in cy:
        return [{"m": "主板", "n": 13}, {"m": "科创板", "n": 2},
                {"m": "创业板", "n": 1}]
    if "count(DISTINCT s) AS n" in cy:
        return [{"n": 16}]
    if "r.rel AS k" in cy:
        return [{"k": "投资", "n": 5}, {"k": "增持", "n": 3}]
    if "r.dir AS k" in cy:
        return [{"k": "利好", "n": 7}, {"k": "利空", "n": 2}]
    raise AssertionError("unhandled cypher: %s" % cy[:60])


def test_situ_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(KE, "_q", _situ_q)
    # 文件侧指向 tmp（写一行保证 mtime/count 可读）
    for attr in ("STRUCT_FP", "EVENT_FP", "TEXTS_FP"):
        p = tmp_path / (attr.lower() + ".jsonl")
        p.write_text(json.dumps({"doc_id": "d0"}) + "\n", encoding="utf-8")
        monkeypatch.setattr(KE, attr, str(p))
    s = KE.situ()
    assert s["ok"] and s["neo4j_up"]
    assert s["doc_edges"] == 14 and s["sec_involved"] == 16
    assert s["latest_event_ts"] == "2026-10-09 00:56:59"
    assert s["inv_markets"] == {"主板": 13, "科创板": 2, "创业板": 1}
    assert s["rels"] == {"投资": 5, "增持": 3}
    assert s["dirs"] == {"利好": 7, "利空": 2}
    assert s["struct"]["count"] == 1 and s["texts"]["count"] == 1
    assert s["cadence_min"] == KE.INGEST_CADENCE_MIN


def test_situ_neo4j_down_files_still_reported(monkeypatch, tmp_path):
    """Neo4j 挂 → ok False；文件侧字段独立降级不缺席。"""
    def boom(cy, **kw):
        raise ConnectionError("neo4j down")

    monkeypatch.setattr(KE, "_q", boom)
    p = tmp_path / "structure_shadow.jsonl"
    p.write_text("{\"doc_id\": \"a\"}\n{\"doc_id\": \"b\"}\n", encoding="utf-8")
    monkeypatch.setattr(KE, "STRUCT_FP", str(p))
    for attr in ("EVENT_FP", "TEXTS_FP"):        # 缺失文件
        monkeypatch.setattr(KE, attr, str(tmp_path / "missing.jsonl"))
    s = KE.situ()
    assert s["ok"] is False and "neo4j down" in s["error"]
    assert s["neo4j_up"] is False
    assert s["struct"]["count"] == 2 and s["struct"]["mtime"]
    assert s["event_shadow"] == {"mtime": None, "count": 0}
    assert s["texts"]["mtime"] is None


# ── doc：条级/文档级关联 + key_numbers 序列化（真实 tmp 文件）──

@pytest.fixture()
def _doc_env(tmp_path, monkeypatch):
    texts = tmp_path / "doc_texts.jsonl"
    texts.write_text(json.dumps({
        "doc_id": "dab8c33cd4551", "name": "news.txt", "kind": "txt",
        "src": "news", "ts": "2026-10-08 14:56:42",
        "text_chars": 9, "text": "全文内容abc",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    struct = tmp_path / "structure_shadow.jsonl"
    struct.write_text(json.dumps({
        "doc_id": "dab8c33cd4551i25",
        "doc_type": "新闻快讯", "title_guess": "东吴证券投资华锡有色",
        "direction": "利好", "direction_why": "对外投资布局上游资源",
        "key_numbers": [{"value": "20亿元", "context": "投资额"}],
        "tags": ["投资", "有色"], "companies": ["东吴证券"],
        "events": [{"subject": "东吴证券", "rel": "投资",
                    "object": "华锡有色", "amount": "20亿元"}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(KE, "TEXTS_FP", str(texts))
    monkeypatch.setattr(KE, "STRUCT_FP", str(struct))
    return texts, struct


def test_doc_exact_item_level_hit(_doc_env):
    d = KE.doc("dab8c33cd4551i25")
    assert d["ok"] and d["found_text"] and d["found_struct"]
    assert d["doc_level"] == "dab8c33cd4551"
    assert d["text"] == "全文内容abc" and d["src"] == "news"
    st = d["struct"]
    assert st["title_guess"] == "东吴证券投资华锡有色"
    assert st["direction"] == "利好"
    assert st["key_numbers"] == ["投资额：20亿元"]   # dict → 串化
    assert st["events"][0]["rel"] == "投资"


def test_doc_level_fallback_prefix(_doc_env):
    """struct 只有条级 id，但传入文档级 → 前缀兜底命中。"""
    d = KE.doc("dab8c33cd4551")
    assert d["ok"] and d["found_struct"]
    assert d["found_text"]                          # texts 恒为文档级键


def test_doc_key_numbers_variants(_doc_env, monkeypatch):
    p = _doc_env[1]
    row = json.loads(p.read_text(encoding="utf-8"))
    row["key_numbers"] = [
        {"value": "8票同意", "context": "表决结果"},   # value+context
        {"value": "99%"},                             # 仅 value
        {"context": "无值"},                          # 仅 context
        "裸字符串",                                    # 非 dict
        {},                                           # 空 dict → 剔除
    ]
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    d = KE.doc("dab8c33cd4551i25")
    assert d["struct"]["key_numbers"] == [
        "表决结果：8票同意", "99%", "无值", "裸字符串"]


def test_doc_miss_returns_not_ok(_doc_env):
    d = KE.doc("dffffffffffffi99")
    assert d["ok"] is False and d["found_text"] is False
    assert "未命中" in d["error"]


def test_doc_empty_id(_doc_env):
    d = KE.doc("   ")
    assert d["ok"] is False and d["error"] == "missing doc_id"


# ── api 分发 / 缓存 ─────────────────────────────────────────

def test_api_dispatch_unknown(monkeypatch):
    assert KE.api("nope")["ok"] is False
    assert "unknown kind" in KE.api("nope")["error"]


def test_api_routes(monkeypatch):
    hits = []
    monkeypatch.setattr(KE, "situ", lambda: {"ok": True, "tag": "s"})
    monkeypatch.setattr(KE, "graph", lambda qp: {"ok": True, "tag": "g"})
    monkeypatch.setattr(KE, "list_rows", lambda qp: {"ok": True, "tag": "l"})
    monkeypatch.setattr(KE, "search", lambda qp: {"ok": True, "tag": "q"})
    monkeypatch.setattr(KE, "doc", lambda a: {"ok": True, "tag": "d"})
    assert KE.api("situ")["tag"] == "s"
    assert KE.api("graph", None, {})["tag"] == "g"
    assert KE.api("list", None, {})["tag"] == "l"
    assert KE.api("search", None, {})["tag"] == "q"
    assert KE.api("doc", "x")["tag"] == "d"


def test_load_map_mtime_invalidate(tmp_path):
    fp = tmp_path / "f.jsonl"
    fp.write_text("{\"doc_id\": \"a\"}\n", encoding="utf-8")
    cache = {"mtime": -1.0, "map": {}}
    m1 = KE._load_map(str(fp), cache)
    assert "a" in m1
    fp.write_text("{\"doc_id\": \"a\"}\n{\"doc_id\": \"b\"}\n",
                  encoding="utf-8")
    m2 = KE._load_map(str(fp), cache)
    assert "b" in m2                                 # mtime 变 → 重读


def test_load_map_missing_file():
    cache = {"mtime": 123.0, "map": {"stale": 1}}
    m = KE._load_map(r"Z:\nope\missing.jsonl", cache)
    assert m == {} and cache["mtime"] == -1.0       # 缺失 → 清缓存
