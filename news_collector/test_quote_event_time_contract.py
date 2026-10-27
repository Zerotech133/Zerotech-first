# -*- coding: utf-8 -*-
"""自选行情 v2 原生事件时间离线验收；严格禁止真实网络与投递。"""

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("quote_snapshot.py")
SPEC = importlib.util.spec_from_file_location("quote_snapshot_under_test", MODULE_PATH)
quote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quote)


def a_share_fields(date="2026-10-19", time="10:00:00", current="10.50"):
    """构造 32 项新浪 A 股响应。"""
    fields = [""] * 32
    fields[0] = "样本"
    fields[1], fields[2], fields[3] = "10", "10", current
    fields[4], fields[5], fields[8], fields[9] = "11", "9", "100", "1000"
    fields[30], fields[31] = date, time
    return fields


def test_native_time_parsing_and_code_identity():
    rec = quote.parse_one("sh600000", a_share_fields())
    assert rec["ts"] == "2026-10-19 10:00:00"
    assert rec["pct"] == 5.0
    for date, time in (
        ("", ""),
        ("2026-02-30", "10:00:00"),
        ("2026-10-19", "25:00:00"),
        ("2026-10-19", ""),
    ):
        assert quote.parse_one("sh600000", a_share_fields(date, time))["ts"] is None
    assert quote.parse_one("sh600000", a_share_fields()[:30])["ts"] is None
    assert quote.parse_one("shINVALID", a_share_fields()) is None
    assert quote.parse_one("sh600000", a_share_fields(current="0"))["pct"] is None
    assert (
        quote.parse_one(
            "rt_hk00700", ["", "香港样本", "", "", "11", "9", "10", "", "5"]
        )["ts"]
        is None
    )
    assert (
        quote.parse_one("gb_aapl", ["美国样本", "10", "5", "", "", "", "11", "9", ""])[
            "ts"
        ]
        is None
    )


def test_eligibility_closed_on_future_stale_weekend_and_calendar():
    observed = datetime(2026, 10, 19, 10, 5, tzinfo=quote.CST)
    rec = quote.parse_one("sh600000", a_share_fields())
    rec["market_as_of"] = "2026-10-19T10:00:00+08:00"
    assert quote._eligibility(rec, observed) == {
        "eligible": False,
        "reason": "trading_calendar_and_session_unverified",
    }
    rec["market_as_of"] = "2026-10-19T10:06:00+08:00"
    assert quote._eligibility(rec, observed)["reason"] == "future_native_event_time"
    rec["market_as_of"] = "2026-10-19T09:49:59+08:00"
    assert quote._eligibility(rec, observed)["reason"] == "stale_native_event_time"
    rec["market_as_of"] = "2026-10-18T10:00:00+08:00"
    assert (
        quote._eligibility(rec, datetime(2026, 10, 18, 10, 5, tzinfo=quote.CST))[
            "reason"
        ]
        == "non_trading_weekend"
    )
    rec["market_as_of"] = None
    assert quote._eligibility(rec, observed)["reason"] == "missing_native_event_time"
    rec["code"] = "gb_aapl"
    assert quote._eligibility(rec, observed)["reason"] == "unsupported_market_or_code"


@pytest.mark.parametrize("second_run", [False, True])
def test_snapshot_observation_does_not_forge_events_or_push(
    tmp_path: Path, monkeypatch, second_run
):
    """混合源、停牌和陈旧源不会伪造历史行或绕过直接推送。"""
    db_path = tmp_path / "quotes.db"
    status_path = tmp_path / "quotes_status.json"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "watchlist": [
                    {"code": "sh600000"},
                    {"code": "sz000001"},
                    {"code": "bj920001"},
                    {"code": "rt_hk00700"},
                    {"code": "gb_aapl"},
                ],
                "quote_alert_pct": 3,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(quote, "DB", str(db_path))
    monkeypatch.setattr(quote, "STATUS_PATH", str(status_path))
    monkeypatch.setattr(quote, "CFG_PATH", str(config))
    state_path = tmp_path / "alert_state.json"
    state_path.write_text(
        json.dumps({"sh600000": {"down": "2026-10-19 09:30:00"}}), encoding="utf-8"
    )
    monkeypatch.setattr(quote, "ALERT_STATE", str(state_path))
    monkeypatch.setattr(
        quote, "now_cst", lambda: datetime(2026, 10, 19, 10, 5, tzinfo=quote.CST)
    )
    monkeypatch.setattr(quote, "log", lambda message: None)
    attempts = []
    monkeypatch.setattr(
        quote, "push_message", lambda message: attempts.append(message) or True
    )
    lines = [
        'var hq_str_sh600000="' + ",".join(a_share_fields(time="10:04:00")) + '";',
        'var hq_str_sz000001="' + ",".join(a_share_fields(date="", time="")) + '";',
        'var hq_str_bj920001="' + ",".join(a_share_fields(current="0")) + '";',
        'var hq_str_rt_hk00700=",香港样本,,,11,9,10,,5";',
        'var hq_str_gb_aapl="美国样本,10,5,,,,11,9,";',
    ]

    class Response:
        text = "\n".join(lines)
        encoding = "utf-8"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(quote.requests, "get", lambda *args, **kwargs: Response())
    quote.main()
    if second_run:
        quote.main()
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT code, ts, created_at FROM quotes ORDER BY code"
        ).fetchall()
    assert rows == [
        ("bj920001", "2026-10-19 10:00:00", "2026-10-19 10:05:00"),
        ("sh600000", "2026-10-19 10:04:00", "2026-10-19 10:05:00"),
    ]
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["updated"] == "2026-10-19 10:05"
    assert data["observed_at"] == "2026-10-19T10:05:00+08:00"
    assert data["event_time_contract_version"] == 2 and data["count"] == 5
    assert data["alerts_last_run"] == 0 and attempts == []
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "sh600000": {"down": "2026-10-19 09:30:00"}
    }
    items = {item["code"]: item for item in data["items"]}
    assert all(
        item["ts"] is None and item["eligibility"]["eligible"] is False
        for item in items.values()
    )
    assert items["sh600000"]["market_as_of"] == "2026-10-19T10:04:00+08:00"
    assert items["sz000001"]["market_as_of"] is None
    assert items["sz000001"]["eligibility"]["reason"] == "missing_native_event_time"
    assert items["bj920001"]["eligibility"]["reason"] == "halted_or_missing_change"
    assert items["rt_hk00700"]["eligibility"]["reason"] == "unsupported_market_or_code"
    assert items["gb_aapl"]["market_as_of"] is None


def test_freshness_row_never_uses_local_clock_when_source_stale(tmp_path, monkeypatch):
    """即使不合格源时间可留真实历史，重复采集也不产生新的本机事件。"""
    observed = datetime(2026, 10, 19, 10, 5, tzinfo=quote.CST)
    rec = quote.parse_one("sh600000", a_share_fields(time="09:00:00"))
    rec["market_as_of"] = "2026-10-19T09:00:00+08:00"
    assert quote._eligibility(rec, observed)["reason"] == "stale_native_event_time"
    assert rec["ts"] != observed.strftime("%Y-%m-%d %H:%M:%S")
