# -*- coding: utf-8 -*-
"""情报排序/新鲜度回归（2026-10-31 排序拍板，WO-INTEL-PRIVATE-20261031）。

不依赖生产服务/生产库：用 AIOS_ROOT 指向 tmp_path 自建最小 news.db，
验证：
- 总线 news/jev 查询 `ORDER BY ts DESC, created_at DESC, id DESC`
  （旧 `ORDER BY id DESC` 会让旧闻顶第一屏）；
- 总线信封新增 `as_of / latest_ts / age_s` 且与数据一致（无数据时 latest 为 None）；
- 新闻情报台 flash/policy/verdicts 同样按事件时间倒序（verdicts 的 impact/conf
  主排序不变，同分时保持 ts 基础序）；
- 源码静态：两模块新闻 SQL 不再出现 `ORDER BY id DESC`。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_intel_bus  # noqa: E402
import api_news_intel  # noqa: E402

JEV = ('{"bias": "bull", "bias_cn": "利多", "conf": 0.9, '
       '"impact_cn": "中", "scope_cn": "个股"}')
STOCKS = '["600000.SH"]'


def _ts(minutes_ago: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%d %H:%M")


def _created(minutes_ago: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")


def _make_db(root: Path):
    """三行：id 倒序与 ts 倒序相反 + 同 ts 用 created_at 兜底。

    id=9 最旧（30 分钟前，补采/重判场景）：旧 `ORDER BY id DESC` 会让它
    顶到第一屏；id=2/3 同为 5 分钟前，id=2 的 created_at 更新。
    新口径期望顺序：[最新A, 最新B, 旧闻补采]。
    """
    d = root / "news_collector"
    d.mkdir(parents=True)
    db = sqlite3.connect(d / "news.db")
    try:
        db.execute(
            "CREATE TABLE news (id INTEGER PRIMARY KEY, source TEXT, ts TEXT, "
            "created_at TEXT, title TEXT, content TEXT, stocks TEXT, jev TEXT, "
            "hit INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0)")
        db.executemany(
            "INSERT INTO news (id, source, ts, created_at, title, content, "
            "stocks, jev, hit, pushed) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                # 旧闻但 id 最大（补采/重判场景）：旧口径会顶到第一屏
                (9, "src", _ts(30), _created(30), "标题-旧闻补采",
                 "旧闻正文", STOCKS, JEV, 0, 0),
                (2, "src", _ts(5), _created(4), "标题-最新A",
                 "A 正文", STOCKS, JEV, 0, 0),
                (3, "src", _ts(5), _created(6), "标题-最新B",
                 "B 正文", STOCKS, JEV, 0, 0),
            ])
        db.commit()
    finally:
        db.close()


def _expected_titles():
    return ["标题-最新A", "标题-最新B", "标题-旧闻补采"]


def test_bus_news_sorted_by_event_time(tmp_path, monkeypatch):
    root = tmp_path / "aios"
    _make_db(root)
    monkeypatch.setenv("AIOS_ROOT", str(root))
    env, status = api_intel_bus.bus_query(
        {"domain": "news", "days": "7", "limit": "10"})
    assert status == 200 and env["ok"] is True, env.get("errors")
    assert [it["title"] for it in env["items"]] == _expected_titles()
    assert env["count"] == 3


def test_bus_jev_domain_sorted_and_freshness(tmp_path, monkeypatch):
    root = tmp_path / "aios"
    _make_db(root)
    monkeypatch.setenv("AIOS_ROOT", str(root))
    env, status = api_intel_bus.bus_query(
        {"domain": "jev", "days": "7", "limit": "10"})
    assert status == 200 and env["ok"] is True, env.get("errors")
    assert [it["title"] for it in env["items"]] == _expected_titles()
    # 新鲜度信封（2026-10-31 新增）：as_of=查询时刻 / latest_ts=可见最新 / age_s
    assert env["as_of"]
    assert env["latest_ts"] == env["items"][0]["ts"]
    assert isinstance(env["age_s"], int)
    assert 0 <= env["age_s"] < 900, "age_s 应为真实滞后秒（约 5 分钟）"


def test_bus_freshness_no_data_explicit(tmp_path, monkeypatch):
    """无数据时 latest_ts/age_s = None（如实空态，不拿 as_of 冒充）。"""
    root = tmp_path / "aios"
    _make_db(root)
    monkeypatch.setenv("AIOS_ROOT", str(root))
    env, status = api_intel_bus.bus_query(
        {"domain": "news", "code": "000001.SZ", "days": "7", "limit": "10"})
    assert status == 200 and env["ok"] is True
    assert env["items"] == []
    assert env["latest_ts"] is None
    assert env["age_s"] is None
    assert env["as_of"]


def test_news_intel_flash_and_policy_order(tmp_path, monkeypatch):
    root = tmp_path / "aios"
    _make_db(root)
    monkeypatch.setenv("AIOS_ROOT", str(root))
    flash, policy, err = api_news_intel.read_news_columns(("标题",), ())
    assert err is None
    assert [x["title"] for x in flash["items"]] == _expected_titles()
    assert [x["title"] for x in policy["items"]] == _expected_titles()
    assert flash["total"] == 3


def test_news_intel_verdicts_keep_ts_order_on_ties(tmp_path, monkeypatch):
    root = tmp_path / "aios"
    _make_db(root)
    monkeypatch.setenv("AIOS_ROOT", str(root))
    overview, verdicts, err = api_news_intel.read_verdicts()
    assert err is None
    assert overview["total_judged"] == 3 and overview["bull"] == 3
    # impact/conf 全相同 → 稳定排序保持 SQL 基础序（ts DESC 口径）
    assert [x["title"] for x in verdicts["items"]] == _expected_titles()


def test_sql_no_legacy_id_desc_order():
    """静态回归：两模块新闻 SQL 已改事件时间倒序，不得残留 ORDER BY id DESC。

    只看代码行（# 之前），允许注释里解释旧口径（“旧 ORDER BY id DESC …”）。
    """
    for name in ("api_intel_bus.py", "api_news_intel.py"):
        src = (PORTAL / name).read_text(encoding="utf-8")
        assert "ORDER BY ts DESC, created_at DESC, id DESC" in src, name
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            assert "ORDER BY id DESC" not in code, "%s: %s" % (name, line.strip())
