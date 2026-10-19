# -*- coding: utf-8 -*-
"""K-ORG-207：api_news_intel 三栏聚合单测。

monkeypatch env AIOS_ROOT 把 REPO 锚指向 tmp（沿 test_api_notify 判例）；
api_xq_intel.REPORT_ROOT 一并 monkeypatch 指向 tmp（其模块常量锚在
C:/aios 真数据目录，测试必须隔离不读真数据）；FakeHandler 注入 _json
捕获 payload（_json 由 portal_api 主 Handler 提供，单测用 fake 等价替换）。
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_news_intel
import api_perf
import api_xq_intel

NOW = datetime.now()


class FakeHandler(api_news_intel.Handler, api_perf.Handler):
    # api_perf.Handler 仅提供 _norm_qs（portal_api 真机 MRO 里同源），无状态
    def __init__(self):
        self.captured = None
        self.code = 200

    def _json(self, payload, code=200):
        self.captured = payload
        self.code = code


def get(qs=""):
    h = FakeHandler()
    h._news_intel(qs)
    return h


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    monkeypatch.setattr(api_xq_intel, "REPORT_ROOT", tmp_path / "xq_report")
    return tmp_path


def make_db(root, rows):
    (root / "news_collector").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "news_collector" / "news.db")
    con.execute("CREATE TABLE news (id TEXT PRIMARY KEY, ts TEXT, title TEXT,"
                " content TEXT, source TEXT, hit INTEGER DEFAULT 0,"
                " pushed INTEGER DEFAULT 0)")
    con.executemany(
        "INSERT INTO news (id,ts,title,content,source,hit,pushed)"
        " VALUES(?,?,?,?,?,?,?)",
        [(r["id"], r["ts"], r.get("title", ""), r.get("content", ""),
          r.get("source", ""), r.get("hit", 0), r.get("pushed", 0))
         for r in rows])
    con.commit()
    con.close()


def hm(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M")


BASE_ROWS = [
    # 政策条目 × 权威源（govpolicy / chinanews 均在白名单）
    {"id": "govpol:1", "ts": hm(1), "title": "国务院关于XX试点的批复",
     "content": "同意开展试点", "source": "govpolicy", "hit": 1, "pushed": 1},
    {"id": "cns:scroll:1", "ts": hm(2), "title": "央行发布新管理办法征求意见",
     "content": "面向社会征求意见", "source": "chinanews"},
    # 政策条目 × 一般源（sina 不在白名单 → 营养度分层为 false）
    {"id": "sina:1", "ts": hm(3), "title": "央行开展逆回购操作",
     "content": "规模 1000 亿元", "source": "sina", "hit": 0},
    # 非政策条目（不进政策栏，进速报栏）
    {"id": "em:1", "ts": hm(4), "title": "某股盘中涨停",
     "content": "普通快讯", "source": "eastmoney"},
    # 窗口外旧条目（48h 之外，两栏都不收）
    {"id": "sina:old", "ts": hm(72), "title": "国务院旧闻一条",
     "content": "", "source": "sina"},
]


def test_policy_keyword_and_authority_layering(root):
    make_db(root, BASE_ROWS)
    p = get().captured
    assert p["errors"] == {}
    # 政策栏：关键词命中 3 条（旧闻在窗外不算），权威源 2 条
    assert p["policy"]["total"] == 3
    assert p["policy"]["authority_count"] == 2
    by_title = {i["title"]: i["authority"] for i in p["policy"]["items"]}
    assert by_title["国务院关于XX试点的批复"] is True     # govpolicy 权威
    assert by_title["央行发布新管理办法征求意见"] is True  # chinanews 权威
    assert by_title["央行开展逆回购操作"] is False         # sina 一般源
    # 政策条目带 content 供前端点击展开
    assert p["policy"]["items"][0]["content"]


def test_flash_structure_and_window(root):
    make_db(root, BASE_ROWS)
    p = get().captured
    fl = p["flash"]
    assert fl["total"] == 4                  # 窗口内 4 条（旧闻被 48h 过滤）
    assert len(fl["items"]) == 4
    ids = [i["source"] + ":" + (i["title"][:4]) for i in fl["items"]]
    assert all("旧闻" not in t for t in ids)
    # id 倒序：sina:1 > govpol:1 > em:1 > cns:scroll:1
    assert fl["items"][0]["title"].startswith("央行开展逆回购")
    # ts 解析出 hm（HH:MM）+ hit 转 bool
    it = fl["items"][0]
    assert it["hm"] == it["ts"][-5:] and len(it["hm"]) == 5
    assert it["hit"] is False
    gov = next(i for i in fl["items"] if i["source"] == "govpolicy")
    assert gov["hit"] is True and gov["pushed"] is True


def test_fail_visible_when_db_missing(root):
    # 不建 news.db：速报/政策两栏 fail-visible 记 errors，其余照常 200
    h = get()
    p = h.captured
    assert h.code == 200
    assert p["flash"]["items"] == [] and p["policy"]["items"] == []
    assert "flash" in p["errors"] and "policy" in p["errors"]
    assert "news.db" in p["errors"]["flash"]
    # 舆情：报告未生成是合法空态，不记 error
    assert p["sentiment"]["report_exists"] is False
    assert "sentiment" not in p["errors"]
    assert p["generated_at"]


def test_sentiment_summary_and_bad_json(root):
    d = root / "xq_report"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps({
        "date": "2026-10-19", "generated_at": "2026-10-19 19:00:00",
        "ingested_today": 3, "classified_today": 2,
        "sentiment": {"bullish": 1, "neutral": 1, "bearish": 0},
        "top_themes": {"白酒": 1, "算力": 2},
        "top_codes": {"600519": 3},
        "high_confidence": [{"code": "600519", "title": "外资加仓高端白酒",
                             "sentiment": "bullish", "confidence": 0.9,
                             "event_type": "增仓"}],
        "anomaly_signals": [{"code": "300684", "n": 10, "bearish": 6,
                             "baseline6d": 3}],
    }, ensure_ascii=False), encoding="utf-8")
    p = get().captured
    st = p["sentiment"]
    assert st["report_exists"] is True and st["date"] == "2026-10-19"
    assert st["classified_today"] == 2
    assert st["top_themes"] == [["算力", 2], ["白酒", 1]]   # 按计数降序
    assert st["high_confidence"][0]["code"] == "600519"
    assert st["anomaly"][0]["n"] == 10
    assert "sentiment" not in p["errors"]
    # 坏 JSON → fail-visible 记 error，不炸不假绿
    (d / "latest.json").write_text("{bad", encoding="utf-8")
    p2 = get().captured
    assert "sentiment" in p2["errors"] and "bad json" in p2["errors"]["sentiment"]


def test_zero_param_whitelist(root):
    make_db(root, BASE_ROWS)
    assert get("a=1").code == 400
    assert get("").code == 200


def test_config_override_and_builtin_fallback(root):
    make_db(root, BASE_ROWS)
    # 内置默认表兜底（无 news_config.json）：国务院/央行命中照常分类
    p = get().captured
    assert p["policy"]["total"] == 3
    # config 新键覆盖默认表：白名单/关键词整表替换
    cfg = root / "news_collector" / "news_config.json"
    cfg.write_text(json.dumps({
        "policy_keywords": ["特殊词"],
        "policy_authority_sources": ["sina"]}, ensure_ascii=False),
        encoding="utf-8")
    p2 = get().captured
    assert p2["policy"]["total"] == 0          # 默认关键词不再生效
    con = sqlite3.connect(root / "news_collector" / "news.db")
    con.execute("INSERT INTO news (id,ts,title,content,source) VALUES(?,?,?,?,?)",
                ("em:2", hm(1), "关于特殊词的配套细则", "", "sina"))
    con.commit()
    con.close()
    p3 = get().captured
    assert p3["policy"]["total"] == 1
    assert p3["policy"]["items"][0]["authority"] is True   # sina 被配置抬为权威
