# -*- coding: utf-8 -*-
"""W3 飞书推送适配层测试（全部 mock，不发真实消息）。

覆盖：卡片构造（红涨绿跌/纯 URL 按钮）、receiver 解析、hub 鉴权/开关/失败
fail-safe、--push-test 人工批准门、推送失败撤销 alert 以便下 tick 重试。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis import intraday_watch as iw


def _payload(**over) -> dict:
    p = {
        "kind": "watch:realtime", "receiver": "个人", "code": "600337.SH",
        "name": "美克家居", "rule": "pct_cross", "severity": "warn", "critical": False,
        "title": "涨幅突破 5%", "price": 5.06, "pct": 5.2, "amount": 12345.0,
        "vol_ratio": 2.8, "reason": "5 分钟内涨幅 +5.2%", "news": None,
        "sector": None, "ts": "2026-11-05 10:01:00", "fingerprint": "fp-1",
        "url": "http://127.0.0.1:8734/sa?code=600337.SH",
        "footer": "研究提示，不构成投资建议",
    }
    p.update(over)
    return p


def _signal(**over) -> iw.Signal:
    s = iw.Signal(code="600337.SH", name="美克家居", rule="pct_cross", severity="warn",
                  title="涨幅突破 5%", detail="5 分钟内涨幅 +5.2%",
                  ts="2026-11-05 10:01:00", fingerprint="fp-1",
                  price=5.06, pct=5.2, amount=12345.0, vol_ratio=2.8)
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _cfg(enabled: bool = True) -> dict:
    return {"enabled": True,
            "push": {"kind": "watch:realtime", "receiver": "个人", "enabled": enabled}}


# ---------------------------------------------------------------- 卡片 / payload

def test_card_up_is_red_and_down_is_green():
    up = iw.build_feishu_card(_payload(pct=5.2))
    assert up["header"]["template"] == "red"
    assert "600337.SH" in up["header"]["title"]["content"]
    text = json.dumps(up, ensure_ascii=False)
    assert "研究提示，不构成投资建议" in text
    assert "查看报告" in text
    # 纯 URL 按钮（open_url），不含 callback 行为
    action = [e for e in up["elements"] if e.get("tag") == "action"][0]
    btn = action["actions"][0]
    assert btn["behaviors"][0]["type"] == "open_url"
    assert "callback" not in json.dumps(btn)

    down = iw.build_feishu_card(_payload(pct=-3.1))
    assert down["header"]["template"] == "green"


def test_to_push_payload_has_no_callback_buttons():
    payloads = iw.to_push_payload([_signal()], cfg=_cfg())
    assert len(payloads) == 1
    p = payloads[0]
    assert p["fingerprint"] == "fp-1"
    assert p["url"].endswith("/sa?code=600337.SH")
    assert "buttons" not in p


# ---------------------------------------------------------------- receiver / secret

def test_resolve_receiver_by_name(tmp_path):
    path = tmp_path / "receivers.json"
    path.write_text(json.dumps({"receivers": [
        {"name": "个人", "type": "open_id", "id": "ou_x"},
        {"name": "家人群", "type": "chat_id", "id": "oc_y"}]}, ensure_ascii=False),
        encoding="utf-8")
    assert iw.resolve_receiver("个人", str(path))["id"] == "ou_x"
    assert iw.resolve_receiver("家人群", str(path))["type"] == "chat_id"
    # 未命中回落第一个（容错）
    assert iw.resolve_receiver("不存在", str(path))["id"] == "ou_x"


def test_push_disabled_returns_error_without_network(monkeypatch):
    called = []
    monkeypatch.setattr(iw, "_http_post_json", lambda *a, **k: called.append(1) or {"ok": True})
    res = iw.push_via_hub([_payload()], cfg=_cfg(enabled=False))
    assert res and res[0]["ok"] is False
    assert "disabled" in res[0]["error"]
    assert called == []


def test_push_missing_secret_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(iw, "_push_hub_key", lambda: "")
    res = iw.push_via_hub([_payload()], cfg=_cfg())
    assert res[0]["ok"] is False
    assert "PUSH_HUB_KEY" in res[0]["error"]


def test_push_success_posts_card_to_hub(monkeypatch):
    captured = {}

    def fake_post(url, obj, headers=None, timeout=10):
        captured.update(url=url, obj=obj, headers=headers)
        return {"ok": True, "via": "hub"}

    monkeypatch.setattr(iw, "_push_hub_key", lambda: "hub-secret")
    monkeypatch.setattr(iw, "_http_post_json", fake_post)
    monkeypatch.setattr(iw, "resolve_receiver",
                        lambda name="个人", path=None: {"name": "个人", "type": "open_id", "id": "ou_x"})
    res = iw.push_via_hub([_payload()], cfg=_cfg())
    assert res[0]["ok"] is True
    assert captured["url"].endswith("/api/send")
    assert captured["headers"]["X-Push-Hub-Key"] == "hub-secret"
    body = captured["obj"]
    assert body["msg_type"] == "interactive"
    assert body["receive_id"] == "ou_x"
    assert body["receive_id_type"] == "open_id"
    assert body["kind"] == "watch:realtime"
    card = json.loads(body["content"])
    assert card["header"]["template"] == "red"


def test_push_network_failure_is_failsafe(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("hub down")

    monkeypatch.setattr(iw, "_push_hub_key", lambda: "hub-secret")
    monkeypatch.setattr(iw, "_http_post_json", boom)
    monkeypatch.setattr(iw, "resolve_receiver",
                        lambda name="个人", path=None: {"name": "个人", "type": "open_id", "id": "ou_x"})
    res = iw.push_via_hub([_payload()], cfg=_cfg())
    assert res[0]["ok"] is False
    assert "hub down" in res[0]["error"]


# ---------------------------------------------------------------- push-test 批准门

def test_push_test_requires_approval(monkeypatch):
    monkeypatch.delenv("WATCH_PUSH_APPROVED", raising=False)
    res = iw.push_test(cfg=_cfg())
    assert res["ok"] is False
    assert "WATCH_PUSH_APPROVED" in res["error"]


def test_push_test_sends_one_card_when_approved(monkeypatch):
    monkeypatch.setenv("WATCH_PUSH_APPROVED", "1")
    sent = []
    monkeypatch.setattr(iw, "push_via_hub",
                        lambda payloads, cfg=None, receiver=None, timeout=10: (
                            sent.append(payloads) or [{"ok": True, "code": "600337.SH"}]))
    res = iw.push_test(cfg=_cfg())
    assert res["ok"] is True
    assert len(sent) == 1 and sent[0][0]["rule"] == "push_test"


# ---------------------------------------------------------------- 失败撤销 alert（重试语义）

def _sig_msg(sig: iw.Signal) -> str:
    return "%s|%s" % (sig.code, sig.rule)


def _run_once_with_push(monkeypatch, tmp_path, push_result):
    db = tmp_path / "watchtower.db"
    monkeypatch.setattr(iw, "STATE_DB", str(db))
    monkeypatch.setattr(iw, "build_pool", lambda **k: ["600337.SH"])
    monkeypatch.setattr(iw, "evaluate", lambda *a, **k: [_signal()])
    monkeypatch.setattr(iw, "to_push_payload", iw.to_push_payload)
    monkeypatch.setattr(iw, "push_via_hub", lambda payloads, cfg=None, **k: push_result)
    monkeypatch.setattr(iw, "load_key_levels", lambda *a, **k: {})
    conf = _cfg(enabled=True)
    conf["enabled"] = True
    out = iw.run_once(dry_run=False, cfg=conf, fetch=False)
    return out, db


def test_push_failure_forgets_alert_for_retry(monkeypatch, tmp_path):
    out, db = _run_once_with_push(monkeypatch, tmp_path, [{"ok": False, "error": "hub down",
                                                           "code": "600337.SH"}])
    assert out["push"][0]["ok"] is False
    con = sqlite3.connect(str(db))
    try:
        alerts = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        sent = con.execute("SELECT sent FROM pushes").fetchone()[0]
    finally:
        con.close()
    assert alerts == 0, "推送失败应撤销 alert，下一 tick 可重试"
    assert sent == 0


def test_push_success_keeps_alert_and_records_sent(monkeypatch, tmp_path):
    out, db = _run_once_with_push(monkeypatch, tmp_path, [{"ok": True, "code": "600337.SH"}])
    assert out["push"][0]["ok"] is True
    con = sqlite3.connect(str(db))
    try:
        alerts = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        sent = con.execute("SELECT sent FROM pushes").fetchone()[0]
    finally:
        con.close()
    assert alerts == 1
    assert sent == 1
