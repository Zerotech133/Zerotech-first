# -*- coding: utf-8 -*-
"""source_names 单测：渠道中文名映射的完整性与降级行为。

老田令 2026-11-07「渠道说中文」——渠道标识不能让用户猜。这里的守卫是
防止**新渠道上线时页面上裸奔英文**：news.db 里出现的每个 source 都必须
有中文名，否则测试失败（而不是等用户看到 xd_news 才发现）。
"""
import re
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import source_names as sn

NEWS_DB = HERE.parent / "news_collector" / "news.db"


def test_all_chinese_names_are_chinese():
    """映射值必须是中文（允许括号/数字），不能残留英文标识。"""
    for sid, cn in sn.SOURCE_CN.items():
        assert cn and cn != sid, "%s 未配中文名" % sid
        assert not re.fullmatch(r"[a-z_]+", cn), "%s 的中文名仍是英文：%s" % (sid, cn)


def test_unknown_source_passthrough():
    """未知渠道原样返回（不编造、不留空）。"""
    assert sn.cn("brand_new_feed") == "brand_new_feed"
    assert sn.cn("") == "未知渠道"
    assert sn.cn(None) == "未知渠道"
    assert sn.desc("brand_new_feed") == ""


def test_attach_adds_cn_and_desc():
    item = sn.attach({"source": "xd_news", "n": 1})
    assert item["source_cn"] == "通达信新闻"
    assert "数据中台" in item["source_desc"]
    # 未知渠道也有 cn（原样），但不硬造 desc
    it2 = sn.attach({"source": "mystery"})
    assert it2["source_cn"] == "mystery" and "source_desc" not in it2


@pytest.mark.skipif(not NEWS_DB.exists(), reason="无生产 news.db（CI 环境）")
def test_every_db_source_has_chinese_name():
    """库中真实存在的每个 source 都要有中文名——新渠道不配就红。"""
    con = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True)
    try:
        rows = con.execute("SELECT source, COUNT(*) FROM news GROUP BY source"
                           ).fetchall()
    finally:
        con.close()
    missing = [(s, n) for s, n in rows if s and s not in sn.SOURCE_CN]
    assert not missing, "以下渠道缺中文名（会裸露英文）：%s" % missing


@pytest.mark.skipif(not NEWS_DB.exists(), reason="无生产 news.db（CI 环境）")
def test_db_coverage_counts():
    """抽查主力渠道映射到预期中文（口径钉死，改名即报警）。"""
    expect = {"sina": "新浪财经", "eastmoney": "东方财富",
              "wallstreetcn": "华尔街见闻", "announcement": "交易所公告",
              "chinanews": "中国新闻网", "world": "海外媒体",
              "xd_news": "通达信新闻", "xd_notice": "通达信公告",
              "report": "机构研报", "survey": "调研纪要",
              "govpolicy": "政府政策"}
    con = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True)
    try:
        present = {s for (s,) in con.execute("SELECT DISTINCT source FROM news")}
    finally:
        con.close()
    for sid in present & set(expect):
        assert sn.cn(sid) == expect[sid]
