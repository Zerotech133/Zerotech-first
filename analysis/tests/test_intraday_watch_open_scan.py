# -*- coding: utf-8 -*-
"""开盘首扫固定摘要（open_scan）测试：每日一次、失败重试、时段/开关/卡片渲染。"""
import datetime
import json

import pytest

import analysis.intraday_watch as w


def _conf(**over):
    conf = json.loads(json.dumps(w.DEFAULT_CONFIG))
    conf["push"].update({"kind": "watch:realtime", "receiver": "个人", "enabled": True})
    conf.update(over)
    return conf


def _quote(code="600337.SH", name="ST美克", price=3.36, pct=2.44, amount=28816310.0,
           vol_ratio=0.84):
    return {"code": code, "name": name, "price": price, "pct": pct,
            "amount": amount, "vol_ratio": vol_ratio,
            "ts": "2026-11-05 09:25:30"}


def _now(hh=9, mm=26):
    return datetime.datetime(2026, 11, 5, hh, mm, 0, tzinfo=w.CST)


@pytest.fixture()
def store(tmp_path):
    st = w.StateStore(str(tmp_path / "state.db"))
    yield st
    st.close()


def test_payload_fields():
    p = w.build_open_scan_payload([_quote()], ["600337.SH", "605258.SH"],
                                  [{"name": "半导体设备", "pct": 3.1}],
                                  trigger_count=2, cfg=_conf(), now=_now())
    assert p["rule"] == "open_scan"
    assert p["title"].startswith("开盘首扫")
    assert p["pool_size"] == 2
    assert p["trigger_count"] == 2
    assert "本轮触发 2 条" in p["reason"]
    assert p["quotes"][0]["code"] == "600337.SH"
    assert p["sector_top"][0]["name"] == "半导体设备"
    assert p["url"].endswith("/sa")


def test_card_renders_rows_and_sector():
    p = w.build_open_scan_payload([_quote()], ["600337.SH"],
                                  [{"name": "半导体设备", "pct": 3.1}],
                                  trigger_count=0, cfg=_conf(), now=_now())
    card = w.build_feishu_card(p)
    assert card["header"]["template"] == "blue"
    text = json.dumps(card, ensure_ascii=False)
    assert "3.36" in text and "+2.44%" in text
    assert "2882 万" in text and "0.84" in text
    assert "半导体设备" in text
    assert "本轮无触发" in text
    assert "研究提示，不构成投资建议" in text


def test_renders_empty_quotes_gracefully():
    p = w.build_open_scan_payload([], ["600337.SH"], [], trigger_count=0,
                                  cfg=_conf(), now=_now())
    card = w.build_feishu_card(p)
    text = json.dumps(card, ensure_ascii=False)
    assert "暂未取到有效行情" in text


def test_sends_once_per_day(store):
    calls = []

    def sender(payloads, cfg=None):
        calls.append(payloads)
        return [{"ok": True}]

    conf = _conf()
    r1 = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 1, conf, _now(),
                           push_fn=sender)
    assert r1 and r1["ok"] is True
    assert len(calls) == 1
    # 同日第二轮：已发过，不再发
    r2 = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 1, conf, _now(9, 27),
                           push_fn=sender)
    assert r2 is None
    assert len(calls) == 1
    assert store.get_kv("open_scan_done:2026-11-05")


def test_before_0925_not_sent(store):
    calls = []
    conf = _conf()
    r = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(9, 24),
                          push_fn=lambda p, cfg=None: calls.append(p) or [{"ok": True}])
    assert r is None and calls == []


def test_after_close_not_sent(store):
    calls = []
    conf = _conf()
    r = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(15, 10),
                          push_fn=lambda p, cfg=None: calls.append(p) or [{"ok": True}])
    assert r is None and calls == []


def test_push_disabled_not_sent(store):
    calls = []
    conf = _conf()
    conf["push"]["enabled"] = False
    r = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(),
                          push_fn=lambda p, cfg=None: calls.append(p) or [{"ok": True}])
    assert r is None and calls == []
    assert store.get_kv("open_scan_done:2026-11-05") is None


def test_dry_run_not_sent(store):
    calls = []
    conf = _conf()
    r = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(),
                          dry_run=True,
                          push_fn=lambda p, cfg=None: calls.append(p) or [{"ok": True}])
    assert r is None and calls == []


def test_no_quotes_not_sent(store):
    calls = []
    conf = _conf()
    r = w.maybe_open_scan(store, [], ["600337.SH"], [], 0, conf, _now(),
                          push_fn=lambda p, cfg=None: calls.append(p) or [{"ok": True}])
    assert r is None and calls == []
    assert store.get_kv("open_scan_done:2026-11-05") is None


def test_failure_retries_next_tick(store):
    state = {"fail": True, "calls": 0}

    def sender(payloads, cfg=None):
        state["calls"] += 1
        return [{"ok": not state["fail"], "error": "hub down"}]

    conf = _conf()
    r1 = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(),
                           push_fn=sender)
    assert r1 and r1["ok"] is False
    assert store.get_kv("open_scan_done:2026-11-05") is None
    state["fail"] = False
    r2 = w.maybe_open_scan(store, [_quote()], ["600337.SH"], [], 0, conf, _now(9, 27),
                           push_fn=sender)
    assert r2 and r2["ok"] is True
    assert state["calls"] == 2
    assert store.get_kv("open_scan_done:2026-11-05")
