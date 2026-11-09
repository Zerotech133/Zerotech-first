# -*- coding: utf-8 -*-
"""S2 / V4 根因修复回归单测：公告结构化前缀 → news.stocks 兜底。

覆盖：
1. `_ann_prefix_ts` 各板块 / 非法码 / 无前缀 / 前缀在标题中间 / 多码取首个；
2. `_row_stocks` 上游 ts_code / symbol 优先，缺失才走前缀兜底；
3. 与 `tools/jev_r3_probe.py::PREFIX_RE` 解析口径一致；
4. 输出一定被次日 07:10 夜跑的 `jev_audit.norm_ts_code` 接受；
5. AST 契约：`feed_source`(v1) 与 `feed_source_v2` 各自调用一次 `_row_stocks`；
6. 集成：v1 / v2 两条真实入库路径在临时 sqlite 上各写一次，stocks 为带后缀 TS 码。

只读真源：集成测试用 tmp_path 的独立 sqlite，不连 news.db / jev_audit.db。
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "news_collector"))
sys.path.insert(0, str(ROOT / "tools"))

import jev_feed as jf  # noqa: E402
from jev_audit import norm_ts_code  # noqa: E402
from jev_r3_probe import PREFIX_RE  # noqa: E402

VALID = [
    ("[600095 湘财股份]关于回购公司股份的公告", "600095.SH"),
    ("[000016 深康佳A]关于重大资产重组的公告", "000016.SZ"),
    ("[300750 宁德时代]2026年三季报", "300750.SZ"),
    ("[688981 中芯国际]临时公告", "688981.SH"),
    ("[920285 N远大]向不特定合格投资者公开发行股票", "920285.BJ"),
    ("[832479 某公司]北交所公告", "832479.BJ"),
]
INVALID = [
    "普通新闻标题没有前缀",
    "[999999 未知板块]x",        # norm_ts_code 不接受 9 开头（非 920）
    "[60095 位数不足]x",         # 5 位
    "[abcdef 非法码]x",
    "",
]


@pytest.mark.parametrize("title, expected", VALID)
def test_ann_prefix_ts_valid(title, expected):
    assert jf._ann_prefix_ts(title) == expected


@pytest.mark.parametrize("title", INVALID)
def test_ann_prefix_ts_invalid(title):
    assert jf._ann_prefix_ts(title) == ""


def test_prefix_in_middle_and_first_of_multiple():
    assert jf._ann_prefix_ts("快讯｜[600095 湘财股份]回购进展") == "600095.SH"
    # 多码取第一个（与 PREFIX_RE.search 同语义）
    assert jf._ann_prefix_ts("[600095 湘财股份][000016 深康佳A]联合公告") == "600095.SH"


def test_row_stocks_upstream_priority():
    assert jf._row_stocks({"ts_code": "600519.SH"}, "[000016 深康佳A]x") == ["600519.SH"]
    assert jf._row_stocks({"symbol": "000001"}, "[600095 湘财股份]x") == ["000001"]


def test_row_stocks_prefix_fallback():
    assert jf._row_stocks({}, "[600095 湘财股份]关于回购") == ["600095.SH"]
    assert jf._row_stocks({"ts_code": ""}, "[920285 N远大]上市") == ["920285.BJ"]


def test_row_stocks_empty_when_nothing():
    assert jf._row_stocks({}, "普通新闻标题") == []
    assert jf._row_stocks({}, "[999999 未知]x") == []


@pytest.mark.parametrize("title, _", VALID)
def test_regex_aligned_with_r3_probe(title, _):
    a = jf.ANN_PREFIX_RE.search(title)
    b = PREFIX_RE.search(title)
    assert (a.groups() if a else None) == (b.groups() if b else None)


@pytest.mark.parametrize("title, expected", VALID)
def test_output_accepted_by_nightly_norm_ts_code(title, expected):
    ts = jf._ann_prefix_ts(title)
    assert ts == expected
    assert norm_ts_code(ts) == ts, "次日 07:10 夜跑会丢弃该码"


def test_no_external_master_dependency():
    src = (ROOT / "news_collector" / "jev_feed.py").read_text(encoding="utf-8")
    assert "_names.json" not in src and "stock_names.json" not in src


def test_both_insert_sites_call_row_stocks():
    src = (ROOT / "news_collector" / "jev_feed.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("feed_source", "feed_source_v2"):
        assert name in fns
        calls = [n for n in ast.walk(fns[name])
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_row_stocks"]
        assert len(calls) == 1, "%s 应恰好调用一次 _row_stocks" % name
        assert any(isinstance(a, ast.Name) and a.id == "title" for a in calls[0].args)


@pytest.fixture
def temp_news_db(tmp_path, monkeypatch):
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, "
        "content TEXT, hit INT, pushed INT, digested INT, created_at TEXT, "
        "stocks TEXT, jev TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(jf, "DB", str(p))
    return p


def _notice_df():
    import pandas as pd
    return pd.DataFrame([{
        "title": "[600095 湘财股份]关于回购公司股份的公告",
        "url": "http://example.test/notice/1",
        "pub_date": "20261106",
        "content": "正文",
    }])


def _read_stocks(path):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute("SELECT stocks FROM news").fetchall()
    finally:
        con.close()
    return [json.loads(r[0]) for r in rows]


def test_v1_feed_source_writes_prefix_code(temp_news_db, monkeypatch):
    df = _notice_df()
    monkeypatch.setattr(jf.dm, "read", lambda *a, **k: df)
    n_ins, n_fetched, _max_date, err = jf.feed_source("notice", "notice", None, 2, 500)
    assert err is None and n_ins == 1
    assert _read_stocks(temp_news_db) == [["600095.SH"]]


def test_v2_feed_source_writes_prefix_code(temp_news_db, monkeypatch):
    df = _notice_df()
    monkeypatch.setattr(jf.dm, "read", lambda *a, **k: df)
    n_ins, _n_fetched, _new_wm, err = jf.feed_source_v2("notice", "notice", None, 2, 500)
    assert err is None and n_ins == 1
    assert _read_stocks(temp_news_db) == [["600095.SH"]]


def test_upstream_code_still_wins_in_real_insert(temp_news_db, monkeypatch):
    import pandas as pd
    df = pd.DataFrame([{
        "title": "[600095 湘财股份]关于回购公司股份的公告",
        "url": "http://example.test/notice/2",
        "pub_date": "20261106",
        "content": "正文",
        "ts_code": "600519.SH",
    }])
    monkeypatch.setattr(jf.dm, "read", lambda *a, **k: df)
    n_ins, _n, _m, err = jf.feed_source("news", "news", None, 2, 500)
    assert err is None and n_ins == 1
    assert _read_stocks(temp_news_db) == [["600519.SH"]]
