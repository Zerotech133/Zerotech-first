# -*- coding: utf-8 -*-
r"""port v1 · 研判卡时效口径（portal/api_jev_interpret._sediment）单元测试。

【判例来源】2026-11-08 老田追问「周一打开是不是又要等 100 秒」。查代码坐实：
  旧判据 `age = 今天 - 卡交易日 <= 1`（自然日）。后果是每个周一、每个节后
  第一天，上一交易日盘后刚生成的卡全部被判过期丢弃 → 用户又被拖进 100 秒
  现算。周日晚实测：卡 trade_date=11-06（周五），age=2 > 1 → 直接 MISS。
  另一处同源缺陷：预生成脚本默认 `date = today`，周末跑出来的卡被标成
  周日（数据实为周五），页面读起来像"周日也有行情"。

【新口径】卡的交易日 >= 「最近一个已收盘交易日」即新鲜——寿命跟交易日走。
  日历同源于 quant_backtest 的 trade_cal（与 facts 的行情源是同一批数据）；
  日历读不到时退回工作日近似（跨 1 个工作日内仍新鲜）。

【硬边界】不联网、不写生产库；交易日历与库路径全部 monkeypatch 注入。

用例清单：
  SF-N01 P0 周末不丢卡：expect 仍是周五时，周五的卡命中
  SF-N02 P0 落后一个交易日即作废：expect 推进到周一后，周五的卡 MISS
  SF-N03 P0 超前卡（周末顺延生成）判新鲜
  SF-N04 P0 日历不可用 → 按工作日阈值（<=1 新鲜，>=2 作废）
  SF-N05 P0 坏日期 / 非 30 日窗 → 不命中，且不抛
  SF-N06 P1 anchor 文案随判定变化（不新鲜时写「＜」，读起来不自相矛盾）
  SF-N07 P1 _weekday_gap 只数工作日，坏输入返回 99
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_jev_interpret as J  # noqa: E402

SCHEMA = """
CREATE TABLE intel_briefs (
  code TEXT NOT NULL, trade_date TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT, tier TEXT, facts TEXT, conclusion TEXT,
  engine TEXT, engine_label TEXT, model TEXT, degrade_note TEXT, built_at TEXT,
  assumptions TEXT, alternatives TEXT, disconfirming_evidence TEXT,
  information_gaps TEXT, early_warning TEXT, feedback_due TEXT,
  PRIMARY KEY (code, trade_date, kind))
"""


def _mkdb(path, rows):
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO intel_briefs (code, trade_date, kind, name, tier, facts,"
        " conclusion, engine, engine_label, model, degrade_note, built_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(path)


def _row(code="300750", td="2026-11-06", built="2026-11-06 18:30:11"):
    return (code, td, "story", "宁德时代", "T2",
            "标的 300750.SZ（宁德时代）：…", "【事件面】…", "cloud",
            "预生成研判卡", "gateway:deepseek/deepseek-flash", None, built)


# ── 判据（纯函数，不碰库）────────────────────────────────────

def test_sf_n01_weekend_keeps_friday_card():
    """SF-N01 周末 expect 仍是周五 → 周五的卡必须命中（旧口径此处 MISS）。"""
    fresh, anchor = J._card_fresh("2026-11-06", "2026-11-06")
    assert fresh is True
    assert "2026-11-06" in anchor


def test_sf_n02_lag_one_trade_day_drops():
    """SF-N02 周一盘后 expect 推进到 11-09 → 周五的卡作废，回落在线重算。"""
    fresh, anchor = J._card_fresh("2026-11-06", "2026-11-09")
    assert fresh is False
    assert "2026-11-06" in anchor and "2026-11-09" in anchor


def test_sf_n03_future_card_is_fresh():
    """SF-N03 卡交易日超前于 expect（周末/节前顺延生成）→ 数据仍是最新。"""
    assert J._card_fresh("2026-11-09", "2026-11-06")[0] is True


def test_sf_n04_calendar_down_uses_weekday_threshold(monkeypatch):
    """SF-N04 日历不可用时按工作日近似：1 个工作日容差，2 个即作废。"""
    monkeypatch.setattr(J, "_weekday_gap", lambda s: 1)
    assert J._card_fresh("2026-11-06", None)[0] is True
    monkeypatch.setattr(J, "_weekday_gap", lambda s: 2)
    fresh, anchor = J._card_fresh("2026-11-06", None)
    assert fresh is False
    assert "工作日" in anchor


def test_sf_n07_weekday_gap_counts_workdays():
    """SF-N07 坏日期返回 99（当过期）；同日为 0；跨周末不多算。"""
    assert J._weekday_gap("bad-date") == 99
    assert J._weekday_gap("") == 99
    # 未来日期的间隔恒为 0（循环不进入），不因是周末而变负
    assert J._weekday_gap("2999-01-01") == 0


def test_sf_n06_anchor_wording_matches_verdict():
    """SF-N06 anchor 用「＜」表达不新鲜，避免"≥ 却判过期"的自相矛盾。"""
    ok = J._card_fresh("2026-11-06", "2026-11-06")[1]
    bad = J._card_fresh("2026-11-06", "2026-11-09")[1]
    assert "≥" in ok and "＜" not in ok
    assert "＜" in bad and "≥" not in bad


# ── 端到端：_sediment 读临时库 ──────────────────────────────

def test_sf_n05_sediment_hits_on_trade_day_not_natural_day(tmp_path, monkeypatch):
    """SF-N05 端到端：同一张 11-06 的卡，expect=11-06 命中、expect=11-09 不命中；
    非 30 日窗、坏日期均安全返回 None（不抛）。"""
    db = _mkdb(tmp_path / "briefs.db", [_row()])
    monkeypatch.setattr(J, "BRIEF_DB", db)

    monkeypatch.setattr(J, "_expect_trade_day", lambda: "2026-11-06")
    s = J._sediment("300750", 30)
    assert s and s["sediment"]["trade_date"] == "2026-11-06"
    assert s["sediment"]["expect_trade_date"] == "2026-11-06"
    assert s["conclusion"] == "【事件面】…"

    monkeypatch.setattr(J, "_expect_trade_day", lambda: "2026-11-09")
    assert J._sediment("300750", 30) is None

    # 只认 30 日窗：别的窗口走在线，不读卡
    monkeypatch.setattr(J, "_expect_trade_day", lambda: "2026-11-06")
    assert J._sediment("300750", 7) is None
    # 无卡股
    assert J._sediment("600519", 30) is None


def test_sf_n05b_sediment_survives_bad_rows(tmp_path, monkeypatch):
    """SF-N05b 库不存在 / 日期坏 → None，绝不抛（卡库可重建）。"""
    monkeypatch.setattr(J, "BRIEF_DB", str(tmp_path / "nope.db"))
    assert J._sediment("300750", 30) is None

    db = _mkdb(tmp_path / "bad.db", [
        _row(code="300750", td="2026-13-99"),
        _row(code="000001", td="2026-11-06"),
    ])
    monkeypatch.setattr(J, "BRIEF_DB", db)
    monkeypatch.setattr(J, "_expect_trade_day", lambda: "2026-11-06")
    assert J._sediment("300750", 30) is None      # 坏日期当无卡
    assert J._sediment("000001", 30) is not None  # 同库正常行不受影响
