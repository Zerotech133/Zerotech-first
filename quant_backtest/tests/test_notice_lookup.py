# -*- coding: utf-8 -*-
"""notice_lookup 公告文本查询层 + badnews_lookup 防退化守卫。

背景：2026-11-06 复核发现 badnews_lookup 的 C 段把「本地公告表没入库」
写死在源码里，而库里已有 22 万条公告（最新 2026-11-05）。
本测试守两件事：① 双通道查询与降级语义正确；② 那句硬编码结论不得回潮。
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notice_lookup as nl  # noqa: E402
from warehouse_read_guard import PublicationPending  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
BADNEWS = HERE / "badnews_lookup.py"
INFO_DB = HERE / "data" / "tdx" / "tdx_info.db"


# ---------- 关键词归类 ----------

def test_classify_multi_tag_for_compound_title():
    tags = nl.classify("关于2025年年度报告问询函的回复公告")
    assert "问询函" in tags
    assert "定期报告" in tags


def test_classify_dingzeng_uses_official_wording():
    """公告正文从不用「定增」二字，要用「向特定对象发行」。"""
    assert "定增" in nl.classify("关于向特定对象发行股票预案的公告")
    assert nl.classify("关于独立董事提名人声明") == []


def test_classify_jianchi_not_confused_with_zengchi():
    assert nl.classify("关于股东减持计划的预披露公告") == ["减持"]
    assert nl.classify("关于控股股东增持股份的公告") == ["增持"]


# ---------- 双通道合并 ----------

def _patch_channels(monkeypatch, sqlite_rows, parquet_df):
    monkeypatch.setattr(nl.tdxr, "read_info", lambda *a, **k: sqlite_rows)
    monkeypatch.setattr(nl.tdxr, "read_range", lambda *a, **k: parquet_df)


def test_collect_merges_and_dedupes(monkeypatch):
    sqlite_rows = [
        {"notice_date": "2026-11-05", "title": "关于股东减持计划的公告",
         "notice_type": "股东减持", "url": "http://x/1"},
    ]
    parquet_df = pd.DataFrame(
        [
            {"ts_code": "600519.SH", "ann_date": "20261105",
             "name": "贵州茅台", "title": "关于股东减持计划的公告", "url": "http://x/1"},
            {"ts_code": "600519.SH", "ann_date": "20261104",
             "name": "贵州茅台", "title": "关于回购进展的公告", "url": "http://x/2"},
        ]
    )
    _patch_channels(monkeypatch, sqlite_rows, parquet_df)
    res = nl.collect("600519.SH", days=30)
    assert res["stats"] == {"stock_notice": 1, "ts_anns_d": 2}
    assert len(res["hits"]) == 2, "同日同标题必须合并为一条"
    top = res["hits"][0]
    assert top["date"] == "2026-11-05"
    assert sorted(top["channels"]) == ["stock_notice", "ts_anns_d"]
    assert top["tags"] == ["减持"]


def test_degraded_channel_is_not_reported_as_zero(monkeypatch):
    """同步发布态 = 此刻读不了，不是「没有公告」。"""
    def boom(*a, **k):
        raise PublicationPending("publishing")

    monkeypatch.setattr(nl.tdxr, "read_info", boom)
    monkeypatch.setattr(nl.tdxr, "read_range", lambda *a, **k: pd.DataFrame())
    res = nl.collect("600519.SH", days=7)
    assert res["stats"]["stock_notice"] is None
    assert res["degraded"] is True
    text = "\n".join(nl.format_section(res))
    assert "无法判定" in text
    assert "两条通道均可用且无记录" not in text, "降级态不得给出「无公告」结论"


def test_bj_code_falls_back_to_parquet_channel(monkeypatch):
    """北交所需两通道互补：parquet 侧必须能补上。"""
    parquet_df = pd.DataFrame(
        [{"ts_code": "920001.BJ", "ann_date": "20261105", "name": "某北交所",
          "title": "关于收到问询函的公告", "url": "http://b/1"}]
    )
    _patch_channels(monkeypatch, [], parquet_df)
    res = nl.collect("920001.BJ", days=30)
    assert res["stats"]["stock_notice"] == 0
    assert len(res["hits"]) == 1
    assert res["hits"][0]["tags"] == ["问询函"]
    assert res["hits"][0]["channels"] == ["ts_anns_d"]


def test_sqlite_channel_queries_by_six_digit_code(monkeypatch):
    """xtcloud 把北交所标成 .SZ，按全码等值查会永远查不到，必须按 6 位码查。"""
    seen = {}

    def fake_read_info(tbl, codes=None, limit=200, since=None):
        seen["codes"] = list(codes)
        return [{"ts_code": "920508.SZ", "notice_date": "2026-11-04", "name": "殷图网联",
                 "title": "股东提前终止减持计划公告", "notice_type": "股东减持", "url": ""}]

    monkeypatch.setattr(nl.tdxr, "read_info", fake_read_info)
    monkeypatch.setattr(nl.tdxr, "read_range", lambda *a, **k: pd.DataFrame())
    res = nl.collect("920508.BJ", days=30)
    assert seen["codes"] == ["920508"], "必须按 6 位码查，不能带后缀"
    assert len(res["hits"]) == 1
    assert res["suffix_mismatch"] == 1, "后缀错标要被显式记下来"
    assert "板块后缀" in "\n".join(nl.format_section(res))


def test_risk_tags_exclude_info_tags():
    """定期报告/增持属提示性，不得计为风险标签。"""
    assert nl.has_risk({"tags": ["减持"]}) is True
    assert nl.has_risk({"tags": ["定期报告"]}) is False
    assert nl.has_risk({"tags": ["增持"]}) is False
    assert nl.has_risk({"tags": ["定期报告", "问询函"]}) is True


def test_prefix_variant_titles_merge_across_channels(monkeypatch):
    """xtcloud 标题带「公司名:」前缀，tushare 不带——不归一就合并不掉。"""
    sqlite_rows = [{"ts_code": "600519.SH", "notice_date": "2026-11-03", "name": "贵州茅台",
                    "title": "贵州茅台:贵州茅台关于召开业绩说明会的公告", "notice_type": "", "url": "http://em/1"}]
    parquet_df = pd.DataFrame(
        [{"ts_code": "600519.SH", "ann_date": "20261103", "name": "贵州茅台",
          "title": "贵州茅台关于召开业绩说明会的公告", "url": "http://static.cninfo.com.cn/x.PDF"}]
    )
    _patch_channels(monkeypatch, sqlite_rows, parquet_df)
    res = nl.collect("600519.SH", days=30)
    assert len(res["hits"]) == 1, "同一份公告必须合并成一条"
    hit = res["hits"][0]
    assert hit["title"] == "贵州茅台关于召开业绩说明会的公告", "展示标题应去掉重复前缀"
    assert "cninfo.com.cn" in hit["url"], "合并后优先留官方原文链接"


def test_empty_result_says_both_channels_ok(monkeypatch):
    _patch_channels(monkeypatch, [], pd.DataFrame())
    res = nl.collect("600519.SH", days=7)
    text = "\n".join(nl.format_section(res))
    assert "两条通道均可用且无记录" in text
    assert res["degraded"] is False


# ---------- 防退化守卫 ----------

def test_badnews_has_no_hardcoded_empty_claim():
    """禁「未入库 / rows=0」这类写死结论回潮（数据活着，结论不能死）。"""
    src = BADNEWS.read_text(encoding="utf-8")
    assert ("rows" + "=0") not in src
    assert ("未" + "入库") not in src


def test_badnews_drives_notice_from_lookup():
    src = BADNEWS.read_text(encoding="utf-8")
    assert "nl.collect(" in src, "C 段必须走 notice_lookup 查询，不得自带结论"
    assert "notice_lookup as nl" in src


def test_manual_guide_only_in_degraded_branch():
    """人工指引只允许出现在「通道不可用且无命中」的分支里。"""
    src = BADNEWS.read_text(encoding="utf-8")
    idx = src.find("巨潮资讯")
    assert idx > 0, "人工核查指引应保留（降级路径）"
    guard = src.rfind("if not notice", 0, idx)
    assert guard > 0, "指引必须在 not notice['hits'] and degraded 分支内"
    assert "degraded" in src[guard:idx]


# ---------- 真库集成（缺库即跳过） ----------

@pytest.mark.skipif(not INFO_DB.exists(), reason="本地公告库不存在")
def test_real_db_returns_notices_for_liquid_stock():
    res = nl.collect("600519.SH", days=365, limit=50)
    assert res["stats"]["stock_notice"] is not None
    assert res["stats"]["stock_notice"] > 0, "茅台一年内不可能没有公告"
    assert all(h["date"] for h in res["hits"])
