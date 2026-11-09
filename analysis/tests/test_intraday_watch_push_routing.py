# -*- coding: utf-8 -*-
"""推送分级（客户单聊 / 运营群）与 11:30 午间简报测试。

覆盖：routes 解析与旧键别名、payload scope/receiver、push_via_hub 按接收人分组、
接收人不存在/类型不符 fail-closed、首扫与午间落 pushes.receiver、午间时段与每日一次。
全部 mock，不发真实消息。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis import intraday_watch as iw


def _conf(routes: dict | None = None, routes_active: bool = True, **push_over) -> dict:
    push = {"kind": "watch:realtime", "receiver": "个人", "enabled": True,
            "routes_active": routes_active}
    if routes is not None:
        push["routes"] = routes
    push.update(push_over)
    return {"enabled": True, "portal_base": "http://127.0.0.1:8734", "push": push}


def _now(hour: int = 11, minute: int = 31) -> datetime:
    return datetime(2026, 11, 9, hour, minute, 0, tzinfo=iw.CST)


def _quote(code: str = "600337.SH", name: str = "美克家居", pct: float = 5.2) -> dict:
    return {"code": code, "name": name, "price": 5.06, "pct": pct,
            "amount": 123450000.0, "vol_ratio": 2.8}


def _receivers(name: str, typ: str, rid: str) -> dict:
    return {"name": name, "type": typ, "id": rid}


def test_routes_default_to_single_receiver():
    r = iw._push_routes(_conf())
    assert r == {"customer": "个人", "ops": "个人"}


def test_routes_customer_and_ops():
    conf = _conf({"customer": "个人", "ops": "量化群"})
    r = iw._push_routes(conf)
    assert r["customer"] == "个人" and r["ops"] == "量化群"
    # 异动/客户分析 → 单聊
    p = iw.to_push_payload([iw.Signal(code="600337.SH", name="美克家居", rule="vol_ratio",
                                      severity="warn", title="放量", detail="量比 4.1",
                                      ts="2026-11-09 10:00:00", fingerprint="fp",
                                      price=5.0, pct=5.2, amount=1.0, vol_ratio=4.1)],
                           cfg=conf)[0]
    assert p["receiver"] == "个人" and p["scope"] == "customer"
    # 首扫/收盘/午间 → 运营群
    for payload in (
            iw.build_open_scan_payload([_quote()], ["600337.SH"], [], 1, conf, _now(9, 26)),
            iw.build_midday_payload([_quote()], ["600337.SH"], [], conf, _now(11, 31))):
        assert payload["receiver"] == "量化群" and payload["scope"] == "ops"


def test_routes_legacy_alias_alert_brief():
    r = iw._push_routes(_conf({"alert": "个人", "brief": "家人群"}))
    assert r == {"customer": "个人", "ops": "家人群"}


def test_push_via_hub_groups_by_receiver(monkeypatch):
    sent = []
    monkeypatch.setattr(iw, "_push_hub_key", lambda: "hub-secret")
    monkeypatch.setattr(iw, "_http_post_json", lambda url, body, **k: sent.append(body) or {"ok": True})
    monkeypatch.setattr(iw, "resolve_receiver",
                        lambda name="个人", path=None: {
                            "个人": _receivers("个人", "open_id", "ou_x"),
                            "量化群": _receivers("量化群", "chat_id", "oc_y"),
                        }.get(name))
    payloads = [
        {"code": "600337.SH", "rule": "vol_ratio", "receiver": "个人", "scope": "customer",
         "kind": "watch:realtime"},
        {"code": "POOL", "rule": "midday_summary", "receiver": "量化群", "scope": "ops",
         "kind": "watch:realtime"},
    ]
    res = iw.push_via_hub(payloads, cfg=_conf({"customer": "个人", "ops": "量化群"}))
    assert [r["ok"] for r in res] == [True, True]
    assert len(sent) == 2
    assert sent[0]["receive_id"] == "ou_x" and sent[0]["receive_id_type"] == "open_id"
    assert sent[1]["receive_id"] == "oc_y" and sent[1]["receive_id_type"] == "chat_id"


def test_push_unknown_receiver_fails_closed(monkeypatch):
    called = []
    monkeypatch.setattr(iw, "_push_hub_key", lambda: "hub-secret")
    monkeypatch.setattr(iw, "_http_post_json", lambda *a, **k: called.append(1) or {"ok": True})
    # 模拟 resolve_receiver 的“未命中回落第一个”行为：必须被名称校验拦住
    monkeypatch.setattr(iw, "resolve_receiver",
                        lambda name="个人", path=None: _receivers("个人", "open_id", "ou_x"))
    res = iw.push_via_hub([{"code": "POOL", "rule": "midday_summary",
                            "receiver": "不存在的群", "scope": "ops"}],
                          cfg=_conf({"customer": "个人", "ops": "不存在的群"}))
    assert res[0]["ok"] is False
    assert "receiver not found" in res[0]["error"]
    assert called == []


def test_push_receiver_type_mismatch_fails(monkeypatch):
    called = []
    monkeypatch.setattr(iw, "_push_hub_key", lambda: "hub-secret")
    monkeypatch.setattr(iw, "_http_post_json", lambda *a, **k: called.append(1) or {"ok": True})
    monkeypatch.setattr(iw, "resolve_receiver",
                        lambda name="个人", path=None: _receivers(name, "open_id", "ou_x"))
    res = iw.push_via_hub([{"code": "POOL", "rule": "open_scan",
                            "receiver": "个人", "scope": "ops"}],
                          cfg=_conf({"customer": "个人", "ops": "个人"}))
    assert res[0]["ok"] is False
    assert "type mismatch" in res[0]["error"]
    assert called == []


def test_open_scan_pushes_to_ops_and_records_receiver(monkeypatch, tmp_path):
    conf = _conf({"customer": "个人", "ops": "量化群"})
    seen = []

    def fake_push(payloads, cfg=None, receiver=None, timeout=10):
        seen.extend(p["receiver"] for p in payloads)
        return [{"ok": True}]

    monkeypatch.setattr(iw, "market_open", lambda now=None, cfg=None: True)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    try:
        r = iw.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 1, conf,
                               _now(9, 26), push_fn=fake_push)
        assert r and r["ok"] is True
        assert seen == ["量化群"]
        row = store.con.execute(
            "SELECT receiver FROM pushes WHERE rule = 'open_scan'").fetchone()
        assert row[0] == "量化群"
    finally:
        store.close()


def test_midday_payload_and_card():
    conf = _conf({"customer": "个人", "ops": "量化群"})
    store = iw.StateStore(path=":memory:")
    try:
        store.con.execute(
            "INSERT INTO alerts(ts, trade_date, code, rule, severity, fingerprint, critical) "
            "VALUES (?,?,?,?,?,?,?)",
            ("2026-11-09 10:00:00", "2026-11-09", "600337.SH", "vol_ratio", "warn", "f1", 0))
        store.con.commit()
        p = iw.build_midday_payload(
            [_quote(pct=5.2), _quote("605258.SH", "协和电子", -2.1),
             _quote("688023.SH", "安恒信息", 1.0), _quote("603667.SH", "五洲新春", -0.5)],
            ["600337.SH", "605258.SH"], [{"name": "白银", "pct": 2.87}],
            conf, _now(11, 31), store=store)
    finally:
        store.close()
    assert p["rule"] == "midday_summary" and p["scope"] == "ops"
    assert p["receiver"] == "量化群"
    assert p["top"][0]["code"] == "600337.SH"
    assert p["bottom"][0]["code"] == "605258.SH"
    assert p["alerts_total"] == 1 and p["rules"][0]["rule"] == "vol_ratio"
    card = iw.build_feishu_card(p)
    text = json.dumps(card, ensure_ascii=False)
    assert "午间简报" in text and "领涨" in text and "领跌" in text and "板块前 3" in text


def test_midday_time_gate_once_and_retry(monkeypatch, tmp_path):
    conf = _conf({"customer": "个人", "ops": "量化群"})
    monkeypatch.setattr(iw, "market_open", lambda now=None, cfg=None: True)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    try:
        ok_calls = []

        def ok_push(payloads, cfg=None, receiver=None, timeout=10):
            ok_calls.append(payloads[0]["receiver"])
            return [{"ok": True}]

        assert iw.maybe_midday_summary(store, [_quote()], ["600337.SH"], [], conf,
                                       _now(11, 29), push_fn=ok_push) is None
        r1 = iw.maybe_midday_summary(store, [_quote()], ["600337.SH"], [], conf,
                                     _now(11, 31), push_fn=ok_push)
        assert r1 and r1["ok"] is True and ok_calls == ["量化群"]
        assert store.get_kv("midday_done:2026-11-09")
        # 当日第二次不再发
        assert iw.maybe_midday_summary(store, [_quote()], ["600337.SH"], [], conf,
                                       _now(11, 45), push_fn=ok_push) is None

        # 失败不置标记，下一轮重试
        store2 = iw.StateStore(path=str(tmp_path / "wt2.db"))
        try:
            r = iw.maybe_midday_summary(store2, [_quote()], ["600337.SH"], [], conf,
                                        _now(11, 31),
                                        push_fn=lambda *a, **k: [{"ok": False, "error": "hub down"}])
            assert r and r["ok"] is False
            assert store2.get_kv("midday_done:2026-11-09") is None
        finally:
            store2.close()
    finally:
        store.close()


def test_routes_inactive_keeps_old_behavior():
    """配置可提前写好 routes，但 routes_active=false 时必须维持旧行为（全部个人）。"""
    conf = _conf({"customer": "个人", "ops": "量化群"}, routes_active=False)
    assert iw._push_routes(conf) == {"customer": "个人", "ops": "个人"}
    op = iw.build_open_scan_payload([_quote()], ["600337.SH"], [], 1, conf, _now(9, 26))
    assert op["receiver"] == "个人"
    md = iw.build_midday_payload([_quote()], ["600337.SH"], [], conf, _now(11, 31))
    assert md["receiver"] == "个人"


def test_midday_disabled_when_routes_inactive(monkeypatch, tmp_path):
    """routes_active=false 时即使 midday_brief_enabled=true 也不发午间简报。"""
    conf = _conf({"customer": "个人", "ops": "量化群"}, routes_active=False)
    monkeypatch.setattr(iw, "market_open", lambda now=None, cfg=None: True)
    store = iw.StateStore(path=str(tmp_path / "wt.db"))
    calls = []
    try:
        r = iw.maybe_midday_summary(
            store, [_quote()], ["600337.SH"], [], conf, _now(11, 31),
            push_fn=lambda *a, **k: calls.append(1) or [{"ok": True}])
        assert r is None and calls == []
        assert store.get_kv("midday_done:2026-11-09") is None
    finally:
        store.close()
