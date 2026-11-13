# -*- coding: utf-8 -*-
"""公告类口径豁免（2026-11-13 老田裁定 B 案）的行为契约。

守的是三条硬约束——写错了就是造假：

  1. bias 必须是 "na"，**不能是 "neutral"**。写 neutral 等于让下游把
     「没判读」当成「判过了且中性」，正是判例 #43 禁止的自安慰。
  2. 不写 judged_at。judged_at 是「LLM 真判过」的唯一证据；豁免行若写它，
     每日判读量统计会虚高上千条，把真实的产能缺口盖住。
  3. 不带 prob / bias_probs。没有概率就是没有，不编 0.25/0.25/0.5。

另测幂等与可逆——这是"能上生产"的门槛，不是加分项。
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
NC = HERE.parent
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import jev_prefilter as jp  # noqa: E402


@pytest.fixture()
def db():
    """最小 news 表：只保留豁免逻辑会碰的列。"""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE news ("
        " id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " jev TEXT, model TEXT, judged_at TEXT, attempt INTEGER DEFAULT 0)"
    )
    yield con
    con.close()


def _ins(con, i, source, jev=None):
    con.execute(
        "INSERT INTO news (id, source, ts, title, jev, model, judged_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("id%02d" % i, source, "T", "t%d" % i, jev, None, None),
    )
    con.commit()


def _count(con, where, args=()):
    return con.execute("SELECT COUNT(*) FROM news WHERE " + where, args).fetchone()[0]


# ── 契约 1：bias 是 na，不是 neutral ────────────────────────────────────
def test_bias_is_na_not_neutral():
    d = jp.exempt_jev("xd_notice")
    assert d["bias"] == "na"
    assert d["bias"] != "neutral", "豁免行绝不能伪装成已判读的中性"
    assert d["bias_cn"] == "不适用"


def test_src_is_searchable_marker():
    d = jp.exempt_jev("xd_notice")
    assert d["src"] == jp.EXEMPT_SRC == "notice_exempt"
    assert d["exempt"]["policy"] == "JEV_NOTICE_EXEMPT"
    assert d["exempt"]["source"] == "xd_notice"


# ── 契约 2：不带概率 ────────────────────────────────────────────────────
def test_carries_no_probability():
    d = jp.exempt_jev()
    assert "prob" not in d
    assert "bias_probs" not in d


# ── 契约 3：companies 为空，图谱天然跳过 ────────────────────────────────
def test_no_companies():
    assert jp.exempt_jev()["companies"] == []


# ── 源白名单 ────────────────────────────────────────────────────────────
def test_sources_default_is_xd_notice_only(monkeypatch):
    monkeypatch.delenv(jp.EXEMPT_SOURCES_ENV, raising=False)
    assert jp.exempt_sources() == ("xd_notice",)


def test_sources_env_override(monkeypatch):
    monkeypatch.setenv(jp.EXEMPT_SOURCES_ENV, "xd_notice, announcement")
    assert jp.exempt_sources() == ("xd_notice", "announcement")


def test_sources_env_empty_disables(monkeypatch):
    monkeypatch.setenv(jp.EXEMPT_SOURCES_ENV, "")
    assert jp.exempt_sources() == ()


# ── 作用域：只碰指定源、只碰未判行 ──────────────────────────────────────
def test_marks_only_pending_rows_of_target_source(db, monkeypatch):
    monkeypatch.delenv(jp.EXEMPT_SOURCES_ENV, raising=False)
    monkeypatch.delenv(jp.EXEMPT_ENV, raising=False)
    _ins(db, 1, "xd_notice")                      # 待判，应被标
    _ins(db, 2, "xd_notice", jev='{"bias":"bull"}')  # 已判，不许覆盖
    _ins(db, 3, "xd_news")                        # 别的源，不许碰
    _ins(db, 4, "announcement")                   # 默认不在豁免名单

    assert jp.apply_notice_exempt(db) == 1
    assert _count(db, "id='id01' AND model=?", (jp.EXEMPT_SRC,)) == 1
    assert _count(db, "id='id02' AND jev='{\"bias\":\"bull\"}'") == 1
    assert _count(db, "id='id02' AND model IS NULL") == 1
    assert _count(db, "id='id03' AND jev IS NULL") == 1
    assert _count(db, "id='id04' AND jev IS NULL") == 1


# ── 契约 2 实测：judged_at 必须保持 NULL ────────────────────────────────
def test_does_not_write_judged_at(db, monkeypatch):
    monkeypatch.delenv(jp.EXEMPT_SOURCES_ENV, raising=False)
    monkeypatch.delenv(jp.EXEMPT_ENV, raising=False)
    _ins(db, 1, "xd_notice")
    jp.apply_notice_exempt(db)
    assert _count(db, "id='id01' AND judged_at IS NULL") == 1, (
        "judged_at 一旦被写，每日判读量统计就会虚高，掩盖真实产能缺口"
    )


# ── 幂等 ────────────────────────────────────────────────────────────────
def test_idempotent(db, monkeypatch):
    monkeypatch.delenv(jp.EXEMPT_SOURCES_ENV, raising=False)
    monkeypatch.delenv(jp.EXEMPT_ENV, raising=False)
    _ins(db, 1, "xd_notice")
    _ins(db, 2, "xd_notice")
    assert jp.apply_notice_exempt(db) == 2
    assert jp.apply_notice_exempt(db) == 0, "第二轮必须 0：jev 已非空，WHERE 不该再命中"


# ── 开关关闭时完全不动 ──────────────────────────────────────────────────
def test_env_off_is_noop(db, monkeypatch):
    monkeypatch.setenv(jp.EXEMPT_ENV, "0")
    _ins(db, 1, "xd_notice")
    assert jp.apply_notice_exempt(db) == 0
    assert _count(db, "id='id01' AND jev IS NULL") == 1


# ── 可逆：一条 SQL 退回原状 ─────────────────────────────────────────────
def test_revert_sql_restores(db, monkeypatch):
    monkeypatch.delenv(jp.EXEMPT_SOURCES_ENV, raising=False)
    monkeypatch.delenv(jp.EXEMPT_ENV, raising=False)
    _ins(db, 1, "xd_notice")
    jp.apply_notice_exempt(db)
    assert _count(db, "jev IS NOT NULL") == 1

    db.execute("UPDATE news SET jev=NULL, model=NULL WHERE model=?", (jp.EXEMPT_SRC,))
    db.commit()
    assert _count(db, "jev IS NULL AND model IS NULL") == 1, "回退后必须逐字回到原状"


# ── 载荷可被下游正确解析：bias=na 不在 bull/bear/neutral 白名单内 ──────
def test_payload_excluded_by_bias_whitelist():
    """复刻 portal/api_daily_data.py:186 的 `if b in bias:` 语义。"""
    whitelist = {"bull": 0, "bear": 0, "neutral": 0}
    assert jp.exempt_jev()["bias"] not in whitelist, (
        "若 na 落进白名单，情绪统计会把未判读行算成已判读"
    )


def test_payload_is_valid_json():
    s = json.dumps(jp.exempt_jev("xd_notice"), ensure_ascii=False)
    assert json.loads(s)["bias"] == "na"
