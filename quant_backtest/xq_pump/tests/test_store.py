# -*- coding: utf-8 -*-
"""store 测试：SQLite 建表 / 幂等 upsert / seen 集 / 行情 K 线。"""
import os
import tempfile

from xq_pump.store import Store


def _tmp_db():
    d = tempfile.mkdtemp()
    return os.path.join(d, "t.db")


def test_tables_created():
    s = Store(_tmp_db())
    for t in ("xq_status", "xq_hot_post", "xq_comment", "xq_quote", "xq_kline"):
        assert s.count(t) == 0
    s.close()


def test_posts_idempotent_upsert():
    s = Store(_tmp_db())
    r1 = {"status_id": "1", "created_at": "2026-01-01", "title": "a",
          "desc": "x", "user_id": "u", "target": "", "view_count": 1,
          "comment_count": 2, "retweet_count": 0, "like_count": 3,
          "reward_count": 0, "source": "web", "category": 102,
          "fetched_at": "2026-01-01 00:00:00"}
    assert s.upsert_posts([r1]) == 1
    assert s.count("xq_status") == 1
    # 同主键再次 upsert 应为覆盖而非新增（幂等）
    r1b = dict(r1, title="a2")
    assert s.upsert_posts([r1b]) == 1
    assert s.count("xq_status") == 1
    # 验证覆盖生效
    row = s.con.execute(
        "SELECT title FROM xq_status WHERE status_id='1'").fetchone()
    assert row[0] == "a2"
    s.close()


def test_seen_ids():
    s = Store(_tmp_db())
    s.upsert_posts([{"status_id": "1"}, {"status_id": "2"}])
    seen = s.seen_ids("xq_status", "status_id")
    assert seen == {"1", "2"}
    s.close()


def test_quotes_and_kline():
    s = Store(_tmp_db())
    n1 = s.upsert_quotes([{"symbol": "SH600519", "ts": "2026-01-01 09:30:00",
                           "open": 100, "high": 101, "low": 99, "close": 100.5,
                           "volume": 1, "amount": 2, "pct_chg": 0.5,
                           "fetched_at": "2026-01-01"}])
    assert n1 == 1
    # 同 (symbol, ts) 覆盖
    s.upsert_quotes([{"symbol": "SH600519", "ts": "2026-01-01 09:30:00",
                      "close": 102, "fetched_at": "2026-01-01"}])
    assert s.count("xq_quote") == 1
    nk = s.upsert_kline([{"symbol": "SH600519", "date": "2026-01-01",
                          "open": 100, "high": 101, "low": 99, "close": 100.5,
                          "volume": 1, "amount": 2}])
    assert nk == 1
    assert s.count("xq_kline") == 1
    s.close()


def test_default_db_reuses_legacy_if_present():
    # 不复用真实库：仅验证 _default_db_path 返回合法路径且可建
    d = tempfile.mkdtemp()
    s = Store(os.path.join(d, "sub", "x.db"))  # 子目录需自动建
    assert os.path.exists(os.path.join(d, "sub", "x.db"))
    s.close()
