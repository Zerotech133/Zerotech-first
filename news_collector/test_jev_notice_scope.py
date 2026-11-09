# -*- coding: utf-8 -*-
"""RFC-20261107 方案 A+C 回归测试。

A：公告类例行模板白名单（按 source 限定、四道守门、独立开关、可回退 reason）
C：高价值口径分离（_backlog_left_high）+ 取批降权（_batch_order_clause）

约束：全部用 tmp_path / monkeypatch，不连 news.db、不占 ollama_lock、不发飞书。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jev_backfill as jb  # noqa: E402
import jev_prefilter as pf  # noqa: E402

ANN = "xd_notice"
NEWS = "sina"
T_NOTICE = "富临精工:投资者关系活动记录表"
T_INVEST = "神马电力:关于召开2026年第二次临时股东会的通知"
T_DIR = "富临精工:关于回购股份的投资者关系活动记录表"
T_CODE = "[603284 林平发展] 投资者关系活动记录表"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例都从「开关全开」的干净状态起步。"""
    monkeypatch.delenv(pf.NOTICE_ENV, raising=False)
    monkeypatch.delenv(jb.SCOPE_ALERT_ENV, raising=False)
    monkeypatch.delenv(jb.PRIORITY_BATCH_ENV, raising=False)


# ── A：模板与守门 ────────────────────────────────────────────────────────

def test_notice_enabled_only_safe_and_announcement_source():
    assert pf.notice_enabled(ANN, "safe") is True
    assert pf.notice_enabled("announcement", "safe") is True
    assert pf.notice_enabled(NEWS, "safe") is False          # 关键：防其他源误杀
    assert pf.notice_enabled(ANN, "aggressive") is False
    assert pf.notice_enabled(ANN, "off") is False


def test_four_templates_hit():
    for title in (
        T_NOTICE,
        "关于召开2026年第三季度业绩说明会的公告",
        "汇得科技:内部控制评价报告",
        T_INVEST,
    ):
        assert pf.notice_hit(title, ANN, "safe"), title


def test_decide_skip_with_notice_reason():
    r = pf.decide(T_NOTICE, ANN, mode="safe")
    assert r["decision"] == "skip"
    assert r["reason"] == pf.NOTICE_REASON
    assert r["matched"] == "投资者关系活动记录表"


def test_other_source_never_skipped_by_new_templates():
    """实证：该标题在新闻源 23 命中 13 误杀(56.5%)，必须 keep。"""
    r = pf.decide(T_NOTICE, NEWS, mode="safe")
    assert r["decision"] == "keep"
    assert pf.notice_hit(T_NOTICE, NEWS, "safe") is None


def test_directional_word_wins():
    r = pf.decide(T_DIR, ANN, mode="safe")
    assert r["decision"] == "keep"
    assert r["reason"] == "方向性词"


def test_stock_code_guard_wins():
    r = pf.decide(T_CODE, "announcement", mode="safe")
    assert r["decision"] == "keep"
    assert r["reason"] == "含个股代码（保守保留，防误杀）"


def test_existing_safe_template_keeps_old_reason():
    """旧模板命中时 reason 必须是「例行/行情」，否则回退 SQL 会误选历史数据。"""
    r = pf.decide("关于召开股东大会的通知", ANN, mode="safe")
    assert r["decision"] == "skip"
    assert r["reason"] == "例行/行情"


def test_notice_switch_off(monkeypatch):
    monkeypatch.setenv(pf.NOTICE_ENV, "0")
    assert pf.notice_enabled(ANN, "safe") is False
    assert pf.decide(T_NOTICE, ANN, mode="safe")["decision"] == "keep"


def test_notice_switch_variants(monkeypatch):
    for v in ("off", "false", "no", ""):
        monkeypatch.setenv(pf.NOTICE_ENV, v)
        assert pf.notice_enabled(ANN, "safe") is False, v
    monkeypatch.setenv(pf.NOTICE_ENV, "1")
    assert pf.notice_enabled(ANN, "safe") is True


def test_prefilter_jev_schema_and_rollback_marker():
    j = pf.prefilter_jev(T_NOTICE, ANN, mode="safe")
    assert j is not None
    assert j["src"] == "prefilter"
    assert j["bias"] == "neutral"
    assert j["pref"]["reason"] == pf.NOTICE_REASON
    assert j["pref"]["source"] == ANN
    # 契约 v2 字段必须齐全（skip 行也是最终判定）
    for k in ("horizon", "prob", "bias_probs", "meter", "conf", "scope", "event_type"):
        assert k in j, k
    assert pf.is_prefilter_judgment(json.dumps(j, ensure_ascii=False)) is True


def test_zero_false_skip_on_curated_notice_samples():
    """逐模板抽检的最小回归集：公告源守门后命中项必须全部判 neutral 语义。

    真实全量评估（近 30 日 4645 条守门后公告）并集 812 命中 / 0 误杀，
    这里固化 4 条模板的代表样本，防止后续改正则把方向性公告误纳。
    """
    samples = [
        T_NOTICE,
        "关于召开2026年第三季度业绩说明会的公告",
        "汇得科技:内部控制评价报告",
        T_INVEST,
    ]
    for t in samples:
        assert pf.decide(t, ANN, mode="safe")["decision"] == "skip", t
    # 方向性公告一律不得被新模板吞掉
    for t in (
        "关于控股股东部分股权质押的公告",
        "关于竞价回购股份方案的公告",
        "2026年三季度业绩预增公告",
        "关于重大资产重组停牌的公告",
    ):
        assert pf.decide(t, ANN, mode="safe")["decision"] == "keep", t


# ── C：双口径与降权 ──────────────────────────────────────────────────────

def _make_db(path):
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
        "title TEXT, stocks TEXT, jev TEXT)"
    )
    rows = []
    for i in range(5):
        rows.append(("ann%d" % i, "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None))
    for i in range(3):
        rows.append(("news%d" % i, "sina", "2026-11-07 00:05", "普通新闻", "[]", None))
    rows.append(("nullsrc", None, "2026-11-07 00:06", "无来源", "[]", None))
    con.executemany("INSERT INTO news VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_backlog_left_high_excludes_notice(tmp_path):
    db = tmp_path / "news.db"
    _make_db(db)
    assert jb._backlog_left(str(db)) == 9
    assert jb._backlog_left_high(str(db)) == 4   # 3 条 sina + 1 条 source IS NULL


def test_backlog_left_high_switch_off(monkeypatch, tmp_path):
    db = tmp_path / "news.db"
    _make_db(db)
    monkeypatch.setenv(jb.SCOPE_ALERT_ENV, "0")
    assert jb._scope_alert_enabled() is False
    assert jb._backlog_left_high(str(db)) == jb._backlog_left(str(db)) == 9


def test_batch_order_clause_switch(monkeypatch):
    clause, args = jb._batch_order_clause()
    assert clause == "ORDER BY CASE WHEN source IN (?,?) THEN 1 ELSE 0 END, ts DESC LIMIT ?"
    assert args == jb.LOW_PRIORITY_SOURCES
    monkeypatch.setenv(jb.PRIORITY_BATCH_ENV, "0")
    clause, args = jb._batch_order_clause()
    assert clause == "ORDER BY ts DESC LIMIT ?"
    assert args == ()


def test_batch_order_puts_high_value_first(tmp_path):
    db = tmp_path / "news.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    clause, args = jb._batch_order_clause()
    sql = "SELECT id FROM news WHERE (jev IS NULL OR jev='') " + clause
    ids = [r[0] for r in con.execute(sql, tuple(args) + (4,)).fetchall()]
    con.close()
    assert len(ids) == 4
    assert all(not x.startswith("ann") for x in ids), ids   # 公告类排在后面


def test_alert_backlog_dual_scope_message(monkeypatch):
    sent = []
    monkeypatch.setattr(jb.news_ai, "push_message", lambda m: sent.append(m))
    monkeypatch.setattr(jb, "_alert_mark", lambda: None)
    assert jb.alert_backlog(320, 40, 0, left_total=1000) is True
    assert "高价值口径" in sent[0]
    assert "全量积压 1000 条" in sent[0]
    assert "剩余未判断新闻 320 条" in sent[0]


def test_alert_backlog_legacy_call_unchanged(monkeypatch):
    """不传 left_total 时消息必须逐字保持旧格式（既有告警消费方）。"""
    sent = []
    monkeypatch.setattr(jb.news_ai, "push_message", lambda m: sent.append(m))
    monkeypatch.setattr(jb, "_alert_mark", lambda: None)
    jb.alert_backlog(320, 40, 1)
    assert "高价值口径" not in sent[0]
    assert "deadline 提前收工" in sent[0]


def test_decide_skip_uses_injected_left_unchanged():
    """_decide_skip 仍是纯函数；口径切换在调用方完成，不破坏方案 B 单测。"""
    class _L:
        def holder_info(self):
            return {"purpose": "k266_m3", "pid": 1, "alive": True}

    assert jb._decide_skip(_L(), 100) == (True, False)
    assert jb._decide_skip(_L(), 150) == (False, True)
    assert jb._decide_skip(_L(), None) == (True, False)


# ── 三入口一致性：feed 也走 prefilter ────────────────────────────────────

def test_feed_judge_pending_uses_prefilter(monkeypatch, tmp_path):
    import jev_feed as jf

    db = tmp_path / "feed.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
        "title TEXT, stocks TEXT, jev TEXT)"
    )
    con.executemany(
        "INSERT INTO news VALUES (?,?,?,?,?,?)",
        [
            ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None),
            ("n2", "xd_notice", "2026-11-07 00:01", "富临精工:关于中标重大合同的公告", "[]", None),
        ],
    )
    con.commit()
    con.close()

    monkeypatch.setattr(jf, "DB", str(db))
    monkeypatch.setattr(jf, "_in_night_window", lambda: False)
    monkeypatch.setenv("JEV_PREFILTER", "safe")
    monkeypatch.setattr(jf.time, "sleep", lambda _s: None)
    seen = []

    def _fake_batch(items):
        seen.extend(list(items))
        return {it["id"]: {"bias": "bull", "src": "fake"} for it in items}

    monkeypatch.setattr(jf.news_ai, "jev_judge_batch", _fake_batch)

    res = jf.judge_pending("notice", 500)
    assert res["pref_skipped"] == 1
    assert len(seen) == 1 and seen[0]["id"] == "n2"   # 例行公告没进 LLM
    assert seen[0]["source"] == "xd_notice"           # items 带 source

    con = sqlite3.connect(str(db))
    j1 = con.execute("SELECT jev FROM news WHERE id='n1'").fetchone()[0]
    con.close()
    d = json.loads(j1)
    assert d["src"] == "prefilter"
    assert d["pref"]["reason"] == pf.NOTICE_REASON     # 可被 §7 SQL 精确回退


# ── A-FIX：夜窗守卫内粗筛生效（RFC-20261107 §11-A-FIX）──────────────────
# 修复前 judge_pending() 命中 _in_night_window() 直接 return，21:45–07:05
# 的轮次完全不经过 prefilter；本节锁死「夜窗也粗筛、但绝不调 Ollama」。

def _feed_night_db(tmp_path, rows):
    db = tmp_path / "feed_night.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
        "title TEXT, stocks TEXT, jev TEXT)"
    )
    con.executemany("INSERT INTO news VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


def _arm_night(monkeypatch, jf, db):
    """强制夜窗 + 禁止任何 Ollama 调用；返回 batch 调用记录。"""
    monkeypatch.setattr(jf, "DB", str(db))
    monkeypatch.setattr(jf, "_in_night_window", lambda: True)
    monkeypatch.setattr(jf.time, "sleep", lambda _s: None)
    calls = []

    def _boom(items):
        calls.append(items)
        raise AssertionError("夜窗不得调用 news_ai.jev_judge_batch（OS-FIX-JEVFEEDNIGHT）")

    monkeypatch.setattr(jf.news_ai, "jev_judge_batch", _boom)
    return calls


def test_feed_night_prefilter_skips_routine_notice(monkeypatch, tmp_path):
    import jev_feed as jf

    db = _feed_night_db(tmp_path, [
        ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None),
        ("n2", "xd_notice", "2026-11-07 00:01", "富临精工:关于中标重大合同的公告", "[]", None),
        ("x1", "xd_news",   "2026-11-07 00:02", T_NOTICE, "[]", None),
    ])
    calls = _arm_night(monkeypatch, jf, db)
    monkeypatch.setenv("JEV_PREFILTER", "safe")

    res = jf.judge_pending("notice", 500)
    assert res["skipped_night"] is True
    assert res["judged"] == 0 and res["error"] is None
    assert res["pref_skipped"] == 1
    assert calls == [], "夜窗粗筛不得触发任何 Ollama 判读"

    con = sqlite3.connect(str(db))
    j1 = con.execute("SELECT jev FROM news WHERE id='n1'").fetchone()[0]
    j2 = con.execute("SELECT jev FROM news WHERE id='n2'").fetchone()[0]
    jx = con.execute("SELECT jev FROM news WHERE id='x1'").fetchone()[0]
    con.close()
    d = json.loads(j1)
    assert d["src"] == "prefilter"
    assert d["pref"]["reason"] == pf.NOTICE_REASON   # §7 SQL 可精确回退
    assert d["pref"]["source"] == "xd_notice"
    assert j2 is None and jx is None                 # 非例行公告 / 其他源不动


def test_feed_night_prefilter_off_is_zero_cost_noop(monkeypatch, tmp_path):
    """开关关闭时与修复前逐字一致：不连库、不写行、不告警。"""
    import jev_feed as jf

    db = _feed_night_db(tmp_path, [
        ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None),
    ])
    _arm_night(monkeypatch, jf, db)
    monkeypatch.delenv("JEV_PREFILTER", raising=False)
    connects = []
    real_connect = sqlite3.connect

    def _spy(*a, **k):
        connects.append(a)
        return real_connect(*a, **k)

    monkeypatch.setattr(jf.sqlite3, "connect", _spy)
    res = jf.judge_pending("notice", 500)
    assert res["skipped_night"] is True
    assert res["pref_skipped"] == 0 and res["error"] is None
    assert connects == [], "JEV_PREFILTER=off 时夜窗不得连库"


def test_feed_night_prefilter_lock_contention_falls_back_silently(monkeypatch, tmp_path):
    """与 JevNight 争锁时静默回退，绝不让 feed 轮次变红（不新增告警噪声）。"""
    import jev_feed as jf

    db = _feed_night_db(tmp_path, [
        ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None),
    ])
    calls = _arm_night(monkeypatch, jf, db)
    monkeypatch.setenv("JEV_PREFILTER", "safe")

    def _locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(jf.sqlite3, "connect", _locked)
    res = jf.judge_pending("notice", 500)
    assert res["skipped_night"] is True
    assert res["pref_skipped"] == 0
    assert res["judged"] == 0 and res["error"] is None
    assert calls == []


def test_feed_night_prefilter_never_overwrites_existing_jev(monkeypatch, tmp_path):
    """夜批已判行不得被粗筛覆盖（SELECT 过滤 + UPDATE 双守卫）。"""
    import jev_feed as jf

    prior = json.dumps({"src": "jev_night", "bias": "bull"}, ensure_ascii=False)
    db = _feed_night_db(tmp_path, [
        ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", prior),
        ("n2", "xd_notice", "2026-11-07 00:01", T_NOTICE, "[]", None),
    ])
    _arm_night(monkeypatch, jf, db)
    monkeypatch.setenv("JEV_PREFILTER", "safe")

    res = jf.judge_pending("notice", 500)
    assert res["pref_skipped"] == 1
    con = sqlite3.connect(str(db))
    j1 = con.execute("SELECT jev FROM news WHERE id='n1'").fetchone()[0]
    j2 = con.execute("SELECT jev FROM news WHERE id='n2'").fetchone()[0]
    con.close()
    assert j1 == prior
    assert json.loads(j2)["src"] == "prefilter"


def test_feed_night_prefilter_keeps_other_sources(monkeypatch, tmp_path):
    """方案 A 的 source 限定在夜窗路径同样生效：xd_news 不误杀。"""
    import jev_feed as jf

    db = _feed_night_db(tmp_path, [
        ("x1", "xd_news", "2026-11-07 00:00", T_NOTICE, "[]", None),
    ])
    _arm_night(monkeypatch, jf, db)
    monkeypatch.setenv("JEV_PREFILTER", "safe")

    res = jf.judge_pending("news", 500)
    assert res["skipped_night"] is True
    assert res["pref_skipped"] == 0
    con = sqlite3.connect(str(db))
    jx = con.execute("SELECT jev FROM news WHERE id='x1'").fetchone()[0]
    con.close()
    assert jx is None


def test_feed_night_prefilter_respects_cap(monkeypatch, tmp_path):
    """夜窗粗筛同样受 --cap 截断（P0 丢数未修前，覆盖仅限最近 cap 行）。"""
    import jev_feed as jf

    db = _feed_night_db(tmp_path, [
        ("n1", "xd_notice", "2026-11-07 00:00", T_NOTICE, "[]", None),
        ("n2", "xd_notice", "2026-11-07 00:01", T_NOTICE, "[]", None),
    ])
    _arm_night(monkeypatch, jf, db)
    monkeypatch.setenv("JEV_PREFILTER", "safe")

    res = jf.judge_pending("notice", 1)
    assert res["pref_skipped"] == 1
    con = sqlite3.connect(str(db))
    j1 = con.execute("SELECT jev FROM news WHERE id='n1'").fetchone()[0]
    j2 = con.execute("SELECT jev FROM news WHERE id='n2'").fetchone()[0]
    con.close()
    assert j1 is None and json.loads(j2)["src"] == "prefilter"
