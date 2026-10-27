# -*- coding: utf-8 -*-
"""AlertWatch 周末/非交易日跳过（upstream_ok=None）+ 交易日历回退回归。

日历数据是 gitignored 运行时文件（仓库根/portal/data/trade_cal_open_days.json），
缺失时依赖真实日历的断言 skip；trading_cal 的 weekday 回退断言始终执行。
"""

import datetime
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import alert_watch
import trading_cal

CAL = trading_cal.OPEN_DAYS_PATH
requires_cal = pytest.mark.skipif(
    not os.path.isfile(CAL), reason="trade_cal runtime data missing: %s" % CAL
)


# ---------------------------------------------------------------------------
# trading_cal：真实日历（2026-10-23 五 / 10-24 六 / 10-25 日 / 10-26 一开市）
# ---------------------------------------------------------------------------


@requires_cal
def test_trade_cal_weekend_and_monday():
    assert trading_cal.is_trading_day(datetime.date(2026, 10, 24)) is False
    assert trading_cal.is_trading_day(datetime.date(2026, 10, 25)) is False
    assert trading_cal.is_trading_day(datetime.date(2026, 10, 26)) is True


@requires_cal
def test_trade_cal_holidays():
    assert trading_cal.is_trading_day(datetime.date(2026, 10, 1)) is False  # 国庆
    assert trading_cal.is_trading_day(datetime.date(2027, 1, 1)) is False  # 元旦


@requires_cal
def test_last_trading_day_rolls_back_to_friday():
    assert trading_cal.last_trading_day(datetime.date(2026, 10, 25)) == datetime.date(
        2026, 10, 23
    )
    # 开市日本身就是最近交易日
    assert trading_cal.last_trading_day(datetime.date(2026, 10, 26)) == datetime.date(
        2026, 10, 26
    )


@requires_cal
def test_load_open_days_returns_copy():
    days = trading_cal.load_open_days()
    assert isinstance(days, set) and "2026-10-26" in days
    days.add("1999-01-01")  # 修改副本不得污染模块缓存
    assert "1999-01-01" not in trading_cal.load_open_days()


# ---------------------------------------------------------------------------
# trading_cal：日历不可用 → weekday 回退，绝不抛异常
# ---------------------------------------------------------------------------


def test_missing_file_falls_back_to_weekday(tmp_path):
    missing = str(tmp_path / "no_such_calendar.json")
    assert trading_cal.load_open_days(missing) == set()
    assert (
        trading_cal.is_trading_day(datetime.date(2026, 10, 24), path=missing) is False
    )
    assert (
        trading_cal.is_trading_day(datetime.date(2026, 10, 25), path=missing) is False
    )
    assert trading_cal.is_trading_day(datetime.date(2026, 10, 26), path=missing) is True
    assert trading_cal.last_trading_day(
        datetime.date(2026, 10, 25), path=missing
    ) == datetime.date(2026, 10, 23)


def test_bad_json_falls_back_to_weekday(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not a json", encoding="utf-8")
    assert trading_cal.load_open_days(str(bad)) == set()
    assert (
        trading_cal.is_trading_day(datetime.date(2026, 10, 26), path=str(bad)) is True
    )


def test_invalid_schema_falls_back_to_weekday(tmp_path):
    bad = tmp_path / "schema.json"
    bad.write_text(
        json.dumps({"schema": "other/v9", "open_days": "nope"}), encoding="utf-8"
    )
    assert trading_cal.load_open_days(str(bad)) == set()
    assert (
        trading_cal.is_trading_day(datetime.date(2026, 10, 24), path=str(bad)) is False
    )


# ---------------------------------------------------------------------------
# _write_heartbeat：upstream_ok=None（非交易日）必须原样保留，不得被 bool() 吞成 False
# ---------------------------------------------------------------------------


def test_heartbeat_preserves_upstream_ok_none(tmp_path, monkeypatch):
    hb = tmp_path / "heartbeat.json"
    monkeypatch.setattr(alert_watch, "HEARTBEAT_PATH", str(hb))
    alert_watch._write_heartbeat(0, None, 0, "x")
    payload = json.loads(hb.read_text(encoding="utf-8"))
    assert payload["upstream_ok"] is None
    assert payload["rc"] == 0
    assert payload["routes_count"] == 0
    assert payload["note"] == "x"


def test_heartbeat_keeps_bool_true(tmp_path, monkeypatch):
    hb = tmp_path / "heartbeat.json"
    monkeypatch.setattr(alert_watch, "HEARTBEAT_PATH", str(hb))
    alert_watch._write_heartbeat(0, True, 1, "y")
    payload = json.loads(hb.read_text(encoding="utf-8"))
    assert payload["upstream_ok"] is True
    assert payload["routes_count"] == 1


# ---------------------------------------------------------------------------
# _skip_reason：周末/节假日非空，交易日 None
# ---------------------------------------------------------------------------


@requires_cal
def test_skip_reason_weekend_non_empty():
    reason = alert_watch._skip_reason(datetime.date(2026, 10, 24))
    assert reason, "周六必须给出跳过原因"
    assert "non_trading_day" in reason and "2026-10-24" in reason
    assert "按设计静默" in reason


@requires_cal
def test_skip_reason_holiday_non_empty():
    reason = alert_watch._skip_reason(datetime.date(2026, 10, 1))
    assert reason and "2026-10-01" in reason


@requires_cal
def test_skip_reason_trading_day_is_none():
    assert alert_watch._skip_reason(datetime.date(2026, 10, 26)) is None
    assert alert_watch._skip_reason(datetime.datetime(2026, 10, 26, 9, 30)) is None
    assert alert_watch._skip_reason("2026-10-26") is None


# ---------------------------------------------------------------------------
# main()：跳过早于任何 HTTP fetch，不写状态文件，心跳 rc=0/upstream_ok=None
# ---------------------------------------------------------------------------


def test_main_skips_before_http_fetch(tmp_path, monkeypatch, capsys):
    hb = tmp_path / "heartbeat.json"
    state = tmp_path / "alert_watch_state.json"
    monkeypatch.setattr(alert_watch, "HEARTBEAT_PATH", str(hb))
    monkeypatch.setattr(alert_watch, "STATE_PATH", str(state))
    monkeypatch.setattr(
        alert_watch,
        "_skip_reason",
        lambda now=None: (
            "non_trading_day: 2026-10-24 非交易日（trade_cal/周末），"
            "无 EOD 源预期，按设计静默"
        ),
    )

    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("非交易日不得发起 HTTP fetch")

    monkeypatch.setattr(alert_watch.urllib.request, "urlopen", _must_not_fetch)

    assert alert_watch.main() == 0
    out = capsys.readouterr().out
    assert "ALERT_WATCH_SKIP non_trading_day: 2026-10-24" in out
    assert not state.exists(), "非交易日不得写状态文件（保住上次交易日基线）"
    payload = json.loads(hb.read_text(encoding="utf-8"))
    assert payload["rc"] == 0
    assert payload["upstream_ok"] is None
    assert payload["routes_count"] == 0
    assert payload["note"].startswith("non_trading_day:")
