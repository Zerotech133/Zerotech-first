# -*- coding: utf-8 -*-
"""WO-QT-20261021-108 测试：cnopendata 源族补档为 C（消除 grade="?"）。

背景：catalog 新注册 6 张表 source 一律 "cnopendata"，而 kg_source_rating.SOURCES
无任何族的 feeds 认领该值 → build() 里 grade_of_feed.get(source, (source,"?"))
回落，断流统计里出现一个语义上"未定档"的 key（grade="?"）。本工单新增族
cnopendata_open（grade C）把该 source 认领走。

定档依据（写进 basis，本文件钉住三个事实不被悄悄改写）：
  1. 公开/开放原始出处（GitHub / OSF / 人大财税所 / 白鲨在线），无 SLA、无合同保障
  2. 6 张表中 5 张是年度静态历史包、1 张无时间维
  3. 一次性批量落盘接入，非增量订阅
C 的理由是「无 SLA + 静态交付」，与是否收费无关（不是"因为免费所以 C"）。

覆盖四件事：
  ① cnopendata 归到新族且 grade=C（含 stale_by_family 聚合结果）
  ② 既有各族 grade 逐一不变（硬清单钉死）
  ③ 未知源仍回落 "?"（反例：回落逻辑没被顺手改掉）
  ④ graph_sources=[] 不导致 KeyError/异常，且族表 schema 完整
"""

import json
import os
import sys
import tempfile
from datetime import datetime

# 同目录绝对导入（仓库多处同款写法；使本文件在仓库根或子目录下都能跑）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kg_source_rating as ksr  # noqa: E402


NEW_FAM = "cnopendata_open"
NEW_SOURCE = "cnopendata"

# 改前既有 10 族的 grade 硬清单：本工单不得改动其中任何一个
EXISTING_GRADES = {
    "tushare_pro": "B",
    "xtcloud_local": "B",
    "crypto_feed": "C",
    "human_curation": "A",
    "rule_keyword": "B",
    "llm_local": "C",
    "jev_feed": "D",
    "cooccur_stat": "C",
    "chanlun_engine": "B",
    "candle_local": "B",
}

VALID_GRADES = ("A", "B", "C", "D", "E", "F")


# ── 测试脚手架 ────────────────────────────────────────────


def _write_catalog(tmpdir, tables):
    """写一份最小 catalog（含 aliases 空表，与真实结构同形）。"""
    path = os.path.join(tmpdir, "catalog.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"version": 1, "tables": tables, "aliases": {}}, f, ensure_ascii=False
        )
    return path


def _entry(fresh, date_col="year", source=NEW_SOURCE, **kw):
    """catalog 单表条目（默认 cnopendata 年度表）。"""
    e = {
        "source": source,
        "date_col": date_col,
        "freshness": fresh,
        "freq": "Y",
        "rows": 100,
        "files": 1,
        "date_status": "registered",
        "verification_status": "verified",
    }
    e.update(kw)
    return e


def _build_with(monkeypatch, tables):
    """在临时 catalog 下跑 build()，返回完整输出。"""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(ksr, "CATALOG", _write_catalog(td, tables))
        return ksr.build()


def _grade_of_feed():
    """复刻 build() 内的 feeds→(族, 档) 反查表（同一份 SOURCES 推导）。"""
    m = {}
    for fam, meta in ksr.SOURCES.items():
        for f in meta["feeds"]:
            m[f] = (fam, meta["grade"])
    return m


# ── ① cnopendata 归到新族且 grade=C ───────────────────────


def test_new_family_exists_with_grade_c():
    assert NEW_FAM in ksr.SOURCES, "cnopendata 族未注册，断流里仍会是 grade=?"
    assert ksr.SOURCES[NEW_FAM]["grade"] == "C", "档位应为 C，实得 %r" % (
        ksr.SOURCES[NEW_FAM]["grade"],
    )


def test_feeds_claims_cnopendata_source():
    """feeds 必须字面等于 ["cnopendata"]（catalog source 值精确匹配）。"""
    assert ksr.SOURCES[NEW_FAM]["feeds"] == [NEW_SOURCE]
    assert _grade_of_feed()[NEW_SOURCE] == (NEW_FAM, "C"), (
        "source='cnopendata' 未被新族认领：%r" % (_grade_of_feed(),)
    )


def test_graph_sources_is_empty_list():
    """这 6 张表不进知识图谱——graph_sources 必须是空 **列表**（不是 None）。"""
    gs = ksr.SOURCES[NEW_FAM]["graph_sources"]
    assert gs == [] and isinstance(gs, list)


def test_basis_states_three_facts():
    """basis 必须写全三个事实，且不得表述成"因为免费所以 C"。"""
    b = ksr.SOURCES[NEW_FAM]["basis"]
    assert isinstance(b, str) and len(b) >= 40, "basis 过短，证据不足"
    for must in ("SLA", "静态", "批量"):
        assert must in b, "basis 缺少关键事实：%s" % must
    for origin in ("GitHub", "OSF"):
        assert origin in b, "basis 未写原始出处：%s" % origin
    for bad in ("免费", "零成本", "不花钱"):
        assert bad not in b, (
            "定档理由写成价格因果（%s）——C 的依据是「无 SLA + 静态交付」" % bad
        )


def test_cnopendata_tables_grouped_under_new_family(monkeypatch):
    """stale_by_family：cnopendata 年度表聚合到新族（不是裸 source key）。"""
    tables = {
        "czyx": _entry("2014"),
        "fepu": _entry("2024"),
        "firm-level-climate-change-exposure": _entry("2024"),
        "gjzfssfc": _entry("2014", date_col="年"),
        "yspl": _entry("2017", date_col="年份"),
    }
    out = _build_with(monkeypatch, tables)
    fam = out["staleness"]["stale_by_family"]
    assert NEW_FAM in fam, "新族未出现在断流聚合里：%r" % (sorted(fam),)
    assert fam[NEW_FAM] == sorted(tables), "聚合表集合不符：%r" % (fam[NEW_FAM],)
    assert NEW_SOURCE not in fam, "裸 source key 仍在（说明仍走 ? 回落）"
    assert out["sources"][NEW_FAM]["grade"] == "C"
    # 族内表超期只告警不降档（档随证据走，断流是状态）
    assert out["sources"][NEW_FAM]["grade"] == "C"


def test_no_time_dimension_table_still_excluded(monkeypatch):
    """第 6 张表（无 date_col）不进 stale_tables，但不影响族定档。"""
    tables = {
        "chinese-dictionary-of-financial-sentiment": _entry(
            None, date_col=None, rows=9228, files=2
        ),
        "yspl": _entry("2017", date_col="年份"),
    }
    out = _build_with(monkeypatch, tables)
    stale_names = {r["table"] for r in out["staleness"]["stale_tables"]}
    assert "chinese-dictionary-of-financial-sentiment" not in stale_names
    assert out["staleness"]["stale_by_family"][NEW_FAM] == ["yspl"]
    assert out["sources"][NEW_FAM]["grade"] == "C"


def test_current_year_pack_not_stale_but_family_kept(monkeypatch):
    """当年年度包不算断流；族仍以 grade=C 出现在 sources 里（档不因无告警消失）。"""
    now_year = datetime.now().year
    tables = {"fepu": _entry(str(now_year))}
    out = _build_with(monkeypatch, tables)
    assert NEW_FAM not in out["staleness"]["stale_by_family"]
    assert out["sources"][NEW_FAM]["grade"] == "C"


# ── ② 既有族 grade 逐一不变 ───────────────────────────────


def test_existing_family_grades_unchanged():
    for fam, want in EXISTING_GRADES.items():
        assert fam in ksr.SOURCES, "既有族 %s 不见了" % fam
        got = ksr.SOURCES[fam]["grade"]
        assert got == want, "既有族 %s 档位漂移：%r → %r" % (fam, want, got)


def test_existing_family_stale_grouping_unchanged(monkeypatch):
    """既有族的 feeds 与聚合行为不得因新增族而改变（逐族对照）。"""
    before = {
        f: (fam, ksr.SOURCES[fam]["grade"])
        for fam, meta in ksr.SOURCES.items()
        if fam != NEW_FAM
        for f in meta["feeds"]
    }
    assert before["tushare"] == ("tushare_pro", "B")
    assert before["xtcloud"] == ("xtcloud_local", "B")
    assert before["crypto"] == ("crypto_feed", "C")

    tables = {
        "ts_index_daily": _entry(
            "2014", source="xtcloud", date_col="trade_date", freq="D"
        ),
        "limit_list_d": _entry(
            "2014", source="tushare", date_col="trade_date", freq="D"
        ),
        "crypto_daily": _entry(
            "2014", source="crypto", date_col="trade_date", freq="D"
        ),
        "yspl": _entry("2017", date_col="年份"),
    }
    monkeypatch.setattr(ksr, "trade_days_lag", lambda *a, **k: None)
    fam = _build_with(monkeypatch, tables)["staleness"]["stale_by_family"]
    assert fam["xtcloud_local"] == ["ts_index_daily"]
    assert fam["tushare_pro"] == ["limit_list_d"]
    assert fam["crypto_feed"] == ["crypto_daily"]
    assert fam[NEW_FAM] == ["yspl"]


def test_no_feed_claimed_by_two_families():
    """新增族不得与既有族抢同一个 feeds 值（否则 grade 取决于字典顺序）。"""
    owner = {}
    for fam, meta in ksr.SOURCES.items():
        for f in meta["feeds"]:
            assert f not in owner, "feeds=%r 被 %s 与 %s 同时认领" % (
                f,
                owner.get(f),
                fam,
            )
            owner[f] = fam


# ── ③ 反例：未知源仍回落 "?" ──────────────────────────────


def test_unknown_source_still_falls_back_to_question_mark(monkeypatch):
    """source 不在任何 feeds 里的表 → 族名=裸 source、grade="?"（未定档可见）。"""
    tables = {"mystery_table": _entry("2014", source="mystery_feed")}
    out = _build_with(monkeypatch, tables)
    fam = out["staleness"]["stale_by_family"]
    assert fam.get("mystery_feed") == ["mystery_table"], (
        "未知源未回落裸 source key：%r" % (fam,)
    )
    assert "mystery_feed" not in out["sources"], (
        "未知源被错误地写进 sources（应保持未定档）"
    )
    assert out["sources"].get(NEW_FAM, {}).get("grade") == "C"


def test_near_miss_source_not_claimed(monkeypatch):
    """精确匹配：cnopendata2 / cnopendata_open / CnOpenData 都不得被新族 feed 认领。

    注：回落逻辑用**裸 source 名**当族 key，而新族名恰为 "cnopendata_open"，
    故若 catalog 真出现 source="cnopendata_open" 的表，它会同名共用一个 key。
    这是纯粹的**展示层命名重叠**，定档仍由 feeds 精确匹配决定（该 source 不在
    任何 feeds → 未定档），下面两条分别钉住"不认领"与"档位不来自 SOURCES"。
    """
    near = ("cnopendata2", NEW_FAM, "CnOpenData")
    tables = {
        "approx_0": _entry("2014", source=near[0]),
        "approx_1": _entry("2014", source=near[1]),
        "approx_2": _entry("2014", source=near[2]),
    }
    fam = _build_with(monkeypatch, tables)["staleness"]["stale_by_family"]
    for i, src in enumerate(near):
        assert fam.get(src) == ["approx_%d" % i], (
            "近似源 %r 未按裸 source 独立成 key：%r" % (src, fam)
        )
    # 关键：这些近似源都没被新族 feeds 认领（否则会被误判为 C 档）
    lookup = _grade_of_feed()
    for src in near:
        assert src not in lookup, "近似源 %r 被 feeds 误认领 → 会被冒领成 C 档" % src
    assert lookup[NEW_SOURCE] == (NEW_FAM, "C")


def test_grade_lookup_unknown_returns_question_mark():
    """反查表本身对未知 source 返回的是 `?` 默认语义（不在表里）。"""
    m = _grade_of_feed()
    assert NEW_SOURCE in m
    assert m.get("mystery_feed") is None, "未知源不该出现在反查表"
    # build() 的回落表达式：(source, "?") —— 未知源族名=source、档=?
    src = "mystery_feed"
    fam, grade = m.get(src, (src, "?"))
    assert (fam, grade) == (src, "?")


# ── ④ graph_sources=[] 不炸 + 族表 schema 完整 ────────────


def test_empty_graph_sources_no_exception(monkeypatch):
    """graph_sources=[] 的族跑完整 build() 不抛 KeyError/异常。"""
    tables = {"yspl": _entry("2017", date_col="年份")}
    out = _build_with(monkeypatch, tables)  # 不抛异常即通过
    assert out["sources"][NEW_FAM]["graph_sources"] == []
    assert out["sources"][NEW_FAM]["feeds"] == [NEW_SOURCE]


def test_sources_schema_complete_all_families():
    """每个族四字段齐备、类型正确、档位在 A-F 内。"""
    for fam, meta in ksr.SOURCES.items():
        for key in ("grade", "feeds", "graph_sources", "basis"):
            assert key in meta, "族 %s 缺字段 %s" % (fam, key)
        assert meta["grade"] in VALID_GRADES, "族 %s 档位非法：%r" % (
            fam,
            meta["grade"],
        )
        assert isinstance(meta["feeds"], list)
        assert isinstance(meta["graph_sources"], list)
        assert isinstance(meta["basis"], str) and meta["basis"].strip()


def test_build_output_shape_with_new_family(monkeypatch):
    """build() 输出里新族四字段原样透出（供 kg_portal/kg_brief 消费）。"""
    out = _build_with(monkeypatch, {"yspl": _entry("2017", date_col="年份")})
    node = out["sources"][NEW_FAM]
    assert node["grade"] == "C"
    assert node["feeds"] == [NEW_SOURCE]
    assert node["graph_sources"] == []
    assert node["basis"] == ksr.SOURCES[NEW_FAM]["basis"]
    assert isinstance(out["staleness"]["stale_by_family"], dict)
