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
import pytest

import kg_portal as KP


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
