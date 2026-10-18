# -*- coding: utf-8 -*-
"""订阅消费者离线契约：不访问网络、不发飞书、不模拟交易。"""
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "subscribe.py"
SPEC = importlib.util.spec_from_file_location("subscribe_contract_under_test", MODULE_PATH)
subscribe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subscribe)
CST = timezone(timedelta(hours=8))


def stamp(minutes=0):
    """生成相对当前的源事件时刻。"""
    return (datetime.now(CST) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def quote_stamp(minutes=0):
    """生产者实际写入的 CST 分钟精度时间。"""
    return (datetime.now(CST) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")


def test_alerts_missing_unhealthy_and_card_count_do_not_trigger(monkeypatch):
    """展示卡不是可执行信号，健康证据缺失或过期不得推送。"""
    payload = {"alerts": [{"name": "九维警报池", "value": "8 只", "sub": "买入级"}]}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    assert subscribe._sub_alert_signal({}, "intraday") is None
    payload["source_health"] = {"version": 1, "checked_at": stamp(),
                                "sources": {name: {"status": "ok", "as_of": stamp()}
                                            for name in ("premarket", "chanlun", "nine_dim",
                                                         "weak", "cross")}}
    payload["source_health"]["sources"]["nine_dim"]["status"] = "stale"
    assert subscribe._sub_alert_signal({}, "intraday") is None
    payload["source_health"]["sources"]["nine_dim"]["status"] = "ok"
    payload["source_health"]["sources"]["nine_dim"]["as_of"] = stamp(30)
    assert subscribe._sub_alert_signal({}, "intraday") is None
    payload["source_health"]["sources"]["nine_dim"]["as_of"] = stamp()
    subscription = {}
    built = subscribe._sub_alert_signal(subscription, "intraday")
    assert built is not None and "展示卡变化，非盘中买卖点" in built[1]
    assert subscription["_alerts_new"] == {"九维警报池": "8 只"}
    assert subscribe._sub_alert_signal({"_watch_state": subscription["_alerts_new"]}, "intraday") is None


def test_alerts_reject_boolean_version_and_empty_nonzero_mixture(monkeypatch):
    """布尔版本不是 v1；empty 非零卡即使其它源正常也必须整包拒绝。"""
    payload = {"alerts": [{"name": "盘前倾向", "value": "偏多"},
                          {"name": "九维警报池", "value": "8 只"}],
               "source_health": {"version": 1, "checked_at": stamp(),
                                 "sources": {name: {"status": "ok", "as_of": stamp()}
                                             for name in ("premarket", "chanlun", "nine_dim",
                                                          "weak", "cross")}}}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    assert subscribe._sub_alert_signal({}, "intraday") is not None
    payload["source_health"]["version"] = True
    assert subscribe._sub_alert_signal({}, "intraday") is None
    payload["source_health"]["version"] = 1
    payload["source_health"]["sources"]["nine_dim"]["status"] = "empty"
    assert subscribe._sub_alert_signal({}, "intraday") is None
    payload["alerts"][1]["value"] = "0 只"
    accepted = subscribe._sub_alert_signal({}, "intraday")
    assert accepted is not None and len(accepted[2]) == 1
    assert "盘前倾向" in accepted[2][0]
    for name, value in (("缠论信号", "买 2 · 卖 0"),
                        ("弱于板块在案", "1 案"),
                        ("共振做多", "2 只"),
                        ("盘前倾向", "偏多")):
        route = subscribe._ALERT_SOURCES[name]
        payload["source_health"]["sources"]["nine_dim"]["status"] = "ok"
        payload["source_health"]["sources"][route]["status"] = "empty"
        payload["alerts"] = [{"name": "九维警报池", "value": "8 只"},
                             {"name": name, "value": value}]
        assert subscribe._sub_alert_signal({}, "intraday") is None
        payload["source_health"]["sources"][route]["status"] = "ok"


def test_quotes_use_status_not_direction_cooldown(tmp_path, monkeypatch):
    """冷却方向戳不包含幅度；只取实际健康行情快照。"""
    monkeypatch.setattr(subscribe, "NEWS_DIR", str(tmp_path))
    (tmp_path / "quote_alert_state.json").write_text(
        json.dumps({"sh600000": {"up": quote_stamp()}}, ensure_ascii=False), encoding="utf-8")
    assert subscribe._sub_quote_move({}, "intraday") is None
    path = tmp_path / "quotes_status.json"
    data = {"ok": True, "updated": quote_stamp(), "count": 1,
            "alert_threshold": 3.0,
            "items": [{"code": "sh600000", "name": "样本", "pct": 3.5,
                       "ts": quote_stamp(), "price": 10.1}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    subscription = {}
    built = subscribe._sub_quote_move(subscription, "intraday")
    assert built is not None and "sh600000" in built[2][1]
    assert "非独立买卖点" in built[1]
    assert subscribe._sub_quote_move({"_last_fp": subscription["_fp_new"]}, "intraday") is None
    boundary = {**data, "items": [{**data["items"][0], "pct": 25.0}]}
    path.write_text(json.dumps(boundary), encoding="utf-8")
    assert subscribe._sub_quote_move({}, "intraday") is not None
    for patch in ({"ok": False}, {"updated": quote_stamp(30)},
                  {"items": [{**data["items"][0], "ts": quote_stamp(30)}]},
                  {"updated": quote_stamp(-1)},
                  {"items": [{**data["items"][0], "ts": quote_stamp(-1)}]},
                  {"items": [{**data["items"][0], "pct": 1000000}]},
                  {"items": [{**data["items"][0], "pct": -25.01}]},
                  {"alert_threshold": None}, {"count": 0}):
        path.write_text(json.dumps({**data, **patch}), encoding="utf-8")
        assert subscribe._sub_quote_move({}, "intraday") is None


def test_buy_point_daily_close_cannot_claim_intraday_trigger():
    """日线末根不是盘中价格或回踩确认。"""
    assert subscribe._sub_buy_point({"params": {"code": "600000.SH",
           "trigger_kind": "pullback_hold", "trigger_px": 10}}, "intraday") is None
    assert subscribe._sub_buy_point({"params": {"code": "600000.SH",
           "trigger_kind": "break_down", "trigger_px": 10}}, "eod") is None


def test_targets_fail_closed_and_fallback_keeps_identity(tmp_path, monkeypatch):
    """缺失收件人不回落个人；同一目标卡失败后文本仅发该 ID。"""
    monkeypatch.setattr(subscribe, "NEWS_DIR", str(tmp_path))
    assert subscribe._resolve_targets({"receivers": ["失效"]}, {}) == []
    assert subscribe._resolve_targets({}, {}) == []
    (tmp_path / "news_config.json").write_text(json.dumps({"push_targets": [
        {"name": "无 ID", "type": "open_id", "id": None}]}), encoding="utf-8")
    assert subscribe._resolve_targets({}, {}) == []
    targets = subscribe._resolve_targets({"receivers": ["群", "人"]}, {
        "群": {"type": "chat_id", "id": "oc_test"},
        "人": {"type": "open_id", "id": "ou_test"}})
    assert [t["id"] for t in targets] == ["oc_test", "ou_test"]
    sent = []

    def fake_send(receive_id, id_type, content, message_type):
        sent.append((receive_id, id_type, message_type))
        return receive_id == "oc_test" and message_type == "text"

    monkeypatch.setitem(sys.modules, "send_feishu", types.SimpleNamespace(send_content_to=fake_send))
    card = {"header": {"title": {"content": "标题"}},
            "elements": [{"tag": "markdown", "content": "正文"}]}
    result = subscribe._push_card(card, targets)
    assert sent == [("oc_test", "chat_id", "interactive"),
                    ("oc_test", "chat_id", "text"),
                    ("ou_test", "open_id", "interactive"),
                    ("ou_test", "open_id", "text")]
    assert result["_textfb_群"] is True
    assert not subscribe._delivery_ok(result)  # 人失败，不得把整体标记已推并丢掉重试
    assert subscribe._delivery_ok({"群": False, "_textfb_群": True})
    assert not subscribe._delivery_ok({"人": "SEND_FAIL", "_err": "fail"})
    assert subscribe._push_card(card, [{"name": "无 ID", "id": None,
                                       "type": "open_id"}]) == {"_err": "invalid_targets"}


def test_scan_only_records_true_fallback_ack(tmp_path, monkeypatch):
    """真正文本回执驱动配额计数和状态；失败不会写已发指纹。"""
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(subscribe, "STATE_PATH", str(state_path))
    monkeypatch.setattr(subscribe, "load_subs", lambda: [{"id": "s1", "kind": "quote_move",
                         "receivers": ["群"], "when": ["intraday"]}])
    monkeypatch.setattr(subscribe, "load_receivers", lambda: {
        "群": {"type": "chat_id", "id": "oc_test"}})
    monkeypatch.setattr(subscribe, "_BUILDERS", {"quote_move": lambda sub, mode: (
        sub.update({"_fp_new": "fresh"}) or ("标题", "副标题", ["正文"], "blue"))})
    monkeypatch.setattr(subscribe, "_portal_link", lambda *args: "")
    monkeypatch.setattr(subscribe, "_build_card", lambda *args, **kwargs: {"header": {}})
    monkeypatch.setattr(subscribe, "_push_card", lambda *args: {"群": False,
                                                            "_textfb_群": True})
    monkeypatch.setitem(sys.modules, "push_flow", types.SimpleNamespace(
        quota_check=lambda: (None, None, None, False), record=lambda **kwargs: None))
    assert subscribe.run_scan("intraday")["s1"][0] == "ok"
    assert json.loads(state_path.read_text(encoding="utf-8"))["s1"]["last_fp"] == "fresh"
    monkeypatch.setattr(subscribe, "_push_card", lambda *args: {"群": False,
                                                            "_textfb_群": False})
    assert subscribe.run_scan("intraday")["s1"][0] == "fail"
    assert json.loads(state_path.read_text(encoding="utf-8"))["s1"]["count"] == 1
