# -*- coding: utf-8 -*-
"""收盘小结（close_summary）单测：payload 统计、卡片渲染、每日一次、失败重试。

2026-11-06 盘中引擎停摆后补：15:15 收盘小结由引擎在收工前（15:10）自动发送，
不再依赖外部提醒；本文件锁定其统计口径与幂等语义。
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis import intraday_watch as iw  # noqa: E402

CFG = {
    "push": {"enabled": True, "kind": "watch:realtime", "receiver": "个人"},
    "portal_base": "http://127.0.0.1:8734",
}


def _store():
    return iw.StateStore(dry_run=True)


def _insert_alert(store, ts, code, rule, critical=0):
    store.con.execute(
        "INSERT INTO alerts (ts, trade_date, code, rule, severity, fingerprint, "
        "critical, title, detail, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, ts[:10], code, rule, "high", "fp-%s-%s-%s" % (ts, code, rule),
         critical, "t", "d", "{}"))
    store.con.commit()


def _insert_push(store, ts, code, rule, sent):
    store.con.execute(
        "INSERT INTO pushes (ts, code, rule, fingerprint, kind, payload, sent) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts, code, rule, "fp-push", "watch:realtime", "{}", int(bool(sent))))
    store.con.commit()


def test_payload_counts_and_fields():
    store = _store()
    day = "2026-11-06"
    _insert_alert(store, day + " 09:25:03", "605258.SH", "pct_cross", critical=0)
    _insert_alert(store, day + " 09:30:58", "688023.SH", "vol_ratio", critical=0)
    _insert_alert(store, day + " 09:30:58", "688023.SH", "fast_move", critical=0)
    _insert_alert(store, day + " 10:00:21", "605258.SH", "pct_cross", critical=1)
    _insert_alert(store, "2026-11-05 10:00:00", "605258.SH", "pct_cross")  # 非当日
    _insert_push(store, day + " 09:25:03", "605258.SH", "pct_cross", sent=True)
    _insert_push(store, day + " 09:31:00", "688023.SH", "vol_ratio", sent=False)
    now = datetime(2026, 11, 6, 15, 10, 2)
    p = iw.build_closing_summary_payload(store, CFG, now)
    assert p["rule"] == "close_summary"
    assert p["code"] == "POOL"
    assert p["title"].startswith("收盘小结")
    assert p["date"] == day
    assert p["alerts_total"] == 4
    assert p["critical_total"] == 1
    assert p["push_ok"] == 1
    assert p["push_fail"] == 1
    rules = {r["rule"]: r["count"] for r in p["rules"]}
    assert rules == {"pct_cross": 2, "vol_ratio": 1, "fast_move": 1}
    assert p["url"].endswith("/sa")
    assert p["fingerprint"]
    store.close()


def test_card_renders_counts_and_distribution():
    store = _store()
    day = "2026-11-06"
    _insert_alert(store, day + " 09:25:03", "605258.SH", "pct_cross")
    _insert_push(store, day + " 09:25:03", "605258.SH", "pct_cross", sent=True)
    now = datetime(2026, 11, 6, 15, 10, 2)
    p = iw.build_closing_summary_payload(store, CFG, now)
    card = iw.build_feishu_card(p)
    assert card["header"]["template"] == "blue"
    text = "\n".join(e.get("text", {}).get("content", "")
                     for e in card["elements"] if isinstance(e.get("text"), dict))
    assert "触发" in text
    assert "规则分布" in text
    assert "pct cross × 1" in text
    assert "成功 1" in text
    assert "研究提示" in text
    assert any(e.get("tag") == "action" for e in card["elements"])
    store.close()


def test_maybe_closing_summary_sends_once_per_day():
    store = _store()
    sent = []

    def fake_send(payloads, cfg=None, receiver=None, timeout=10):
        sent.append(payloads[0]["rule"])
        return [{"ok": True, "code": "POOL", "rule": "close_summary"}]

    now = datetime(2026, 11, 6, 15, 10, 2)
    r1 = iw.maybe_closing_summary(store, cfg=CFG, now=now, push_fn=fake_send)
    assert r1 and r1["ok"] is True
    r2 = iw.maybe_closing_summary(store, cfg=CFG, now=now, push_fn=fake_send)
    assert r2 is None
    assert sent == ["close_summary"]
    row = store.con.execute(
        "SELECT COUNT(*) FROM pushes WHERE rule = 'close_summary' AND sent = 1").fetchone()
    assert row[0] == 1
    assert store.get_kv("close_summary_done:2026-11-06")
    store.close()


def test_maybe_closing_summary_retries_after_failure():
    store = _store()
    calls = []

    def failing(payloads, cfg=None, receiver=None, timeout=10):
        calls.append("fail")
        return [{"ok": False, "error": "hub down"}]

    def ok(payloads, cfg=None, receiver=None, timeout=10):
        calls.append("ok")
        return [{"ok": True}]

    now = datetime(2026, 11, 6, 15, 10, 2)
    r1 = iw.maybe_closing_summary(store, cfg=CFG, now=now, push_fn=failing)
    assert r1 and r1["ok"] is False
    assert not store.get_kv("close_summary_done:2026-11-06")
    r2 = iw.maybe_closing_summary(store, cfg=CFG, now=now, push_fn=ok)
    assert r2 and r2["ok"] is True
    assert calls == ["fail", "ok"]
    store.close()


def test_maybe_closing_summary_requires_push_enabled():
    store = _store()
    cfg = {"push": {"enabled": False}, "portal_base": ""}
    now = datetime(2026, 11, 6, 15, 10, 2)
    assert iw.maybe_closing_summary(store, cfg=cfg, now=now) is None
    store.close()
