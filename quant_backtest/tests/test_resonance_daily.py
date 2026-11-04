# -*- coding: utf-8 -*-
"""P4 共振 v4 日更闭环测试：resonance_daily（compute_day / daily_update / 事件判定）。

覆盖（工单口径）：
  1) compute_day 空源日诚实：某源零数据 → 该率/gap=None、noise=True、z_roll=None；
  2) compute_day 正常日口径：gap=xq_bull−jev_bull、n<5 噪声、z_roll 用库内前置窗口；
  3) daily_update 幂等：同 dates 跑两遍行数/stats 不变、二遍无新事件；
  4) 新收敛事件判定（夹具库）：昨日 |z_roll|>1 且当日 gap 穿越库内全量均值 → 入 events。

夹具：小 sqlite 造 xq（xq_status × xq_status_classified）/ news（hit=1, jev JSON）两源
+ 小 resonance_events.db（预填历史 gap）+ 小 000905 parquet，不碰真库。
"""
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, "..", "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import resonance_store as rs
import resonance_daily as rd


# ================================================================
# 夹具构造
# ================================================================
def make_xq_db(path, posts):
    """posts: {date: [(sentiment) xN]}"""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE xq_status(status_id TEXT PRIMARY KEY, created_at TEXT)")
    con.execute("CREATE TABLE xq_status_classified(status_id TEXT PRIMARY KEY, sentiment TEXT)")
    for d, sens in posts.items():
        for i, s in enumerate(sens):
            sid = f"{d.replace('-', '')}_{i}"
            con.execute("INSERT INTO xq_status VALUES(?,?)", (sid, f"{d} 1{i:02d}:00:00"))
            con.execute("INSERT INTO xq_status_classified VALUES(?,?)", (sid, s))
    con.commit()
    con.close()


def make_news_db(path, events):
    """events: {date: [(bias) xN]}"""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE news(id TEXT, ts TEXT, hit INT, jev TEXT)")
    for d, biases in events.items():
        for i, b in enumerate(biases):
            con.execute("INSERT INTO news VALUES(?,?,?,?)",
                        (f"{d.replace('-', '')}_{i}", f"{d} 0{i:02d}:30",
                         1, json.dumps({"bias": b, "conf": 0.9, "scope": "test"})))
    con.commit()
    con.close()


def make_index_parquet(path, rows):
    """rows: [(YYYYMMDD, close)]，pct_chg 恒 0。"""
    import pandas as pd
    pd.DataFrame({
        "ts_code": ["000905.SZ"] * len(rows),
        "trade_date": [r[0] for r in rows],
        "close": [r[1] for r in rows],
        "pct_chg": [0.0] * len(rows),
    }).to_parquet(str(path))


def prefill_gaps(db_path, rows):
    """rows: [(date, gap)]，xq_n=10/jev_n=4/noise=0 直接入库。"""
    for d, g in rows:
        rs.update_day(d, gap=g, noise=False, xq_n=10, jev_n=4, db_path=str(db_path))


@pytest.fixture()
def env(tmp_path):
    """通用环境：空源库 + 空 events 库 + 1 行指数 parquet。"""
    xq_db = tmp_path / "xq.db"
    news_db = tmp_path / "news.db"
    db = tmp_path / "res.db"
    pp = tmp_path / "idx.parquet"
    make_index_parquet(pp, [("20260101", 100.0)])
    return {"xq": str(xq_db), "news": str(news_db), "db": str(db),
            "idx": str(pp), "tmp": tmp_path}


# ================================================================
# 1) 空源日诚实
# ================================================================
class TestComputeDayHonest:
    def test_empty_sources_all_none(self, env):
        make_xq_db(env["tmp"] / "xq.db", {})                 # 两源全无当日数据
        make_news_db(env["tmp"] / "news.db", {})
        r = rd.compute_day("2026-02-01", db_path=env["db"],
                           xq_db=env["xq"], news_db=env["news"])
        assert r["xq_n"] == 0 and r["jev_n"] == 0
        assert r["xq_bull"] is None and r["jev_bull"] is None
        assert r["gap"] is None and r["z_roll"] is None
        assert r["noise"] is True                            # 某源零数据 → noise=True

    def test_single_source_missing(self, env):
        # 雪球无帖、JEV 有 → xq_bull/gap=None（诚实），jev_bull 可算，noise=True
        make_xq_db(env["tmp"] / "xq.db", {})
        make_news_db(env["tmp"] / "news.db", {"2026-02-01": ["bull", "bear", "neutral", "bull"]})
        r = rd.compute_day("2026-02-01", db_path=env["db"],
                           xq_db=env["xq"], news_db=env["news"])
        assert r["jev_n"] == 4 and r["jev_bull"] == pytest.approx(0.5)
        assert r["xq_bull"] is None and r["gap"] is None
        assert r["noise"] is True
        # 雪球有帖、JEV 无 → jev_bull/gap=None
        make_xq_db(env["tmp"] / "xq2.db", {"2026-02-01": ["bullish"] * 10})
        make_news_db(env["tmp"] / "news2.db", {})
        r2 = rd.compute_day("2026-02-01", db_path=env["db"],
                            xq_db=str(env["tmp"] / "xq2.db"),
                            news_db=str(env["tmp"] / "news2.db"))
        assert r2["xq_bull"] == pytest.approx(1.0)
        assert r2["jev_bull"] is None and r2["gap"] is None
        assert r2["noise"] is True


# ================================================================
# 2) 正常日口径
# ================================================================
class TestComputeDayNormal:
    def test_gap_noise_and_zroll_window(self, env):
        # 历史前置 5 个有效 gap [0.10..0.50]（01-02..01-06）
        prefill_gaps(env["db"], [(f"2026-01-0{i}", 0.1 * i) for i in range(2, 7)])
        # 当日 01-07：雪球 4 帖（3 bullish/1 bearish）→ n<5 噪声；JEV 4 条（2 bull）→ 0.5
        make_xq_db(env["tmp"] / "xq.db", {"2026-01-07": ["bullish"] * 3 + ["bearish"]})
        make_news_db(env["tmp"] / "news.db",
                     {"2026-01-07": ["bull", "bull", "bear", "neutral"]})
        r = rd.compute_day("2026-01-07", db_path=env["db"],
                           xq_db=env["xq"], news_db=env["news"])
        assert r["xq_n"] == 4 and r["xq_bull"] == pytest.approx(0.75)
        assert r["jev_n"] == 4 and r["jev_bull"] == pytest.approx(0.5)
        assert r["gap"] == pytest.approx(0.25)
        assert r["noise"] is True                            # xq_n=4 < 5
        # z_roll = (0.25−均值)/stdev(库内前置窗口 [0.2..0.6]，即 01-02..01-06)
        import statistics
        win = [0.1 * i for i in range(2, 7)]
        assert r["z_roll"] == pytest.approx((0.25 - statistics.fmean(win)) / statistics.stdev(win))

    def test_zroll_none_when_window_short(self, env):
        prefill_gaps(env["db"], [("2026-01-02", 0.2), ("2026-01-03", 0.3),
                                 ("2026-01-04", 0.4)])       # 仅 3 < 5
        make_xq_db(env["tmp"] / "xq.db", {"2026-01-05": ["bullish"] * 6 + ["bearish"] * 2})
        make_news_db(env["tmp"] / "news.db", {"2026-01-05": ["neutral"] * 4})
        r = rd.compute_day("2026-01-05", db_path=env["db"],
                           xq_db=env["xq"], news_db=env["news"])
        assert r["gap"] == pytest.approx(0.75 - 0.0)
        assert r["z_roll"] is None                           # 窗口不足 5 → None（诚实）


# ================================================================
# 3) daily_update 幂等
# ================================================================
class TestDailyUpdateIdempotent:
    def test_rerun_no_growth(self, env):
        prefill_gaps(env["db"], [("2026-01-02", 0.10), ("2026-01-03", 0.20),
                                 ("2026-01-04", 0.30)])
        make_xq_db(env["tmp"] / "xq.db", {"2026-02-01": ["bullish"] * 7 + ["neutral"] * 3})
        make_news_db(env["tmp"] / "news.db", {"2026-02-01": ["bull", "bear"] * 2})
        kw = dict(dates=["2026-02-01"], db_path=env["db"], xq_db=env["xq"],
                  news_db=env["news"], index_path=env["idx"])
        r1 = rd.daily_update(**kw)
        assert r1["updated_days"] == ["2026-02-01"]
        s1 = rs.stats(env["db"])
        assert s1["n_days"] == 4                             # 3 历史 + 1
        r2 = rd.daily_update(**kw)                           # 二遍：幂等
        assert r2["updated_days"] == ["2026-02-01"]          # 显式 dates 仍重算该日
        assert r2["new_events"] == []
        s2 = rs.stats(env["db"])
        assert s2 == s1                                      # 行数/均值/事件零漂移
        # 值不因重跑漂移
        con = sqlite3.connect(env["db"])
        try:
            row = con.execute("SELECT gap, xq_n, jev_n FROM gap_daily "
                              "WHERE date='2026-02-01'").fetchone()
            assert row[0] == pytest.approx(0.7 - 0.5) and row[1] == 10 and row[2] == 4
        finally:
            con.close()

    def test_default_dates_incremental(self, env):
        """缺省 dates=库内最大日期之后 → end_date（含端点），已入库日不重算。"""
        prefill_gaps(env["db"], [("2026-01-02", 0.10), ("2026-01-03", 0.20)])
        make_xq_db(env["tmp"] / "xq.db", {"2026-01-04": ["bullish"] * 8})
        make_news_db(env["tmp"] / "news.db", {"2026-01-04": ["bull"] * 4})
        r = rd.daily_update(db_path=env["db"], xq_db=env["xq"],
                            news_db=env["news"], index_path=env["idx"],
                            end_date="2026-01-04")
        assert r["updated_days"] == ["2026-01-04"]           # 恰补最大日之后一天
        r2 = rd.daily_update(db_path=env["db"], xq_db=env["xq"],
                             news_db=env["news"], index_path=env["idx"],
                             end_date="2026-01-04")
        assert r2["updated_days"] == []                      # 已最新 → 空
        assert rs.stats(env["db"])["n_days"] == 3


# ================================================================
# 4) 新收敛事件判定（夹具库）
# ================================================================
class TestConvergenceEvent:
    def test_new_event_written_and_fwd_backfilled(self, env):
        # 历史 8 日 gap≈0.30 平稳 → 前一日 0.50 的 z_roll≈12.5（|z|>1）
        hist = [("2026-01-02", 0.28), ("2026-01-03", 0.30), ("2026-01-04", 0.32),
                ("2026-01-05", 0.29), ("2026-01-06", 0.31), ("2026-01-07", 0.30),
                ("2026-01-08", 0.28), ("2026-01-09", 0.32)]
        prefill_gaps(env["db"], hist)
        # 01-10（前日极端）：xq 10 帖全 bullish=1.0，jev 2b/1r/1n=0.5 → gap=0.50
        # 01-11（当日收敛）：xq 6b/2r/2n=0.6，jev 0.5 → gap=0.10，穿越均值 0.30
        make_xq_db(env["tmp"] / "xq.db", {
            "2026-01-10": ["bullish"] * 10,
            "2026-01-11": ["bullish"] * 6 + ["bearish"] * 2 + ["neutral"] * 2,
        })
        make_news_db(env["tmp"] / "news.db", {
            "2026-01-10": ["bull", "bull", "bear", "neutral"],
            "2026-01-11": ["bull", "bull", "bear", "neutral"],
        })
        # 指数：01-12 起连续 6 交易日（close 全 105）→ 01-11 事件锚 01-12，fwd1/3/5 全可填
        make_index_parquet(env["idx"], [(f"2026011{i}", 105.0) for i in range(2, 8)])
        r = rd.daily_update(dates=["2026-01-10", "2026-01-11"], db_path=env["db"],
                            xq_db=env["xq"], news_db=env["news"], index_path=env["idx"])
        assert r["new_events"] == ["2026-01-11"]
        con = sqlite3.connect(env["db"])
        try:
            row = con.execute(
                "SELECT prev_date, prev_gap, gap, pullback, noise, fwd1, fwd3, fwd5 "
                "FROM events WHERE event_date='2026-01-11'").fetchone()
            assert row[0] == "2026-01-10"
            assert row[1] == pytest.approx(0.50) and row[2] == pytest.approx(0.10)
            assert row[3] == pytest.approx(0.40)
            assert row[4] == 0                               # 两日 xq_n≥5 非噪声
            assert row[5] == pytest.approx(0.0)              # fwd 已由 backfill 回填
            assert row[6] == pytest.approx(0.0) and row[7] == pytest.approx(0.0)
            # 前日 z_roll 入库且 |z|>1（extreme）
            z = con.execute("SELECT z_roll FROM gap_daily WHERE date='2026-01-10'").fetchone()[0]
            assert z is not None and abs(z) > 1.0
        finally:
            con.close()
        assert r["backfilled"] == 1
        # 重跑：事件已存在 → 不再计为新事件，fwd 不被清掉
        r2 = rd.daily_update(dates=["2026-01-10", "2026-01-11"], db_path=env["db"],
                             xq_db=env["xq"], news_db=env["news"], index_path=env["idx"])
        assert r2["new_events"] == []
        assert r2["stats"]["n_events"] == 1

    def test_no_event_when_prev_not_extreme(self, env):
        # 前日 z_roll=None（窗口<5）→ 即便 gap 穿越均值也不出事件
        prefill_gaps(env["db"], [("2026-01-02", 0.10), ("2026-01-03", 0.20),
                                 ("2026-01-04", 0.30)])
        make_xq_db(env["tmp"] / "xq.db", {"2026-01-05": ["bullish"] * 5,
                                          "2026-01-06": ["bearish"] * 5})
        make_news_db(env["tmp"] / "news.db", {"2026-01-05": ["neutral"] * 4,
                                              "2026-01-06": ["neutral"] * 4})
        r = rd.daily_update(dates=["2026-01-05", "2026-01-06"], db_path=env["db"],
                            xq_db=env["xq"], news_db=env["news"], index_path=env["idx"])
        assert r["new_events"] == []
        assert r["stats"]["n_events"] == 0
