# -*- coding: utf-8 -*-
"""P0 出口修复单测（2026-10-19 派工单 FB-B / B5）。

离线纪律：monkeypatch 全部网络出口；不真发飞书；不启任何服务；
不触碰 push_secrets.json / work/ 账本（_flow_record 一并 stub）。

覆盖：
  B1 api_send 返回键统一 —— _hub_send 读 why 主键（error 兜底，老
     闸门响应不再被 "hub deny" 吞真因）；
  B2 占位符不可达 + 降级开关 —— _agent_webhook 空/缺键/占位符一律
     返回 ""；fallback 默认开，env 显式关；
  B3 未配置处置 —— fallback 开走 OpenAPI 直发降级；关则拒发
     "no endpoint configured"（不静默）；
  B4 ticker 意图 —— 裸 6 位代码不再落 unclear；确认语义优先；
     短文本不误伤。
"""
import json
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_NC = os.path.join(os.path.dirname(HERE), "news_collector")
for _p in (HERE, _NC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feishu_client  # noqa: E402
import inbound_gateway  # noqa: E402


class _FakeResp:
    """urlopen 替身：构造固定 JSON 响应，零网络。"""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── B1：_hub_send 键名（why 主键 / error 兜底） ──────────────────

def test_hub_send_reads_why_key(monkeypatch):
    monkeypatch.setattr(feishu_client.urllib.request, "urlopen",
                        lambda rq, timeout: _FakeResp(
                            {"ok": False, "why": "silent window"}))
    ok, why, mid = feishu_client._hub_send("x", "text", "ou_x", "open_id")
    assert ok is False
    assert why == "silent window"  # 不再是 "hub deny"


def test_hub_send_error_key_fallback(monkeypatch):
    monkeypatch.setattr(feishu_client.urllib.request, "urlopen",
                        lambda rq, timeout: _FakeResp(
                            {"ok": False, "error": "legacy gate deny"}))
    ok, why, mid = feishu_client._hub_send("x", "text", "ou_x", "open_id")
    assert ok is False
    assert why == "legacy gate deny"  # 老闸门只回 error 时真因不丢


def test_hub_send_ok_with_message_id(monkeypatch):
    monkeypatch.setattr(feishu_client.urllib.request, "urlopen",
                        lambda rq, timeout: _FakeResp(
                            {"ok": True, "why": "hub ok",
                             "message_id": "om_123"}))
    ok, why, mid = feishu_client._hub_send("x", "text", "ou_x", "open_id")
    assert ok is True and why == "hub ok" and mid == "om_123"


# ── B2：占位符不可达 + 降级开关 ──────────────────────────────────

def test_placeholder_url_never_returned(monkeypatch):
    """空 env + secrets 缺键/占位值 → _agent_webhook 必须返回空串，
    占位 URL 在发送代码路径中不可达（只允许比较语境）。"""
    monkeypatch.delenv("PUSH_AGENT_WEBHOOK", raising=False)
    fake_secrets = {"push_agent_webhook": feishu_client.AGENT_WEBHOOK_PLACEHOLDER}
    with mock.patch("builtins.open", mock.mock_open(
            read_data=json.dumps(fake_secrets))):
        feishu_client._AGENT_WEBHOOK_CACHE["ts"] = 0.0
        assert feishu_client._agent_webhook() == ""
    monkeypatch.delenv("PUSH_AGENT_WEBHOOK", raising=False)
    with mock.patch("builtins.open", mock.mock_open(read_data="{}")):
        feishu_client._AGENT_WEBHOOK_CACHE["ts"] = 0.0
        assert feishu_client._agent_webhook() == ""


def test_fallback_enabled_by_default(monkeypatch):
    monkeypatch.delenv(feishu_client.AGENT_WEBHOOK_FALLBACK_ENV, raising=False)
    assert feishu_client.agent_webhook_fallback_enabled() is True
    for v in ("0", "false", "off", "no"):
        monkeypatch.setenv(feishu_client.AGENT_WEBHOOK_FALLBACK_ENV, v)
        assert feishu_client.agent_webhook_fallback_enabled() is False


# ── B3：未配置处置（降级 / 拒发） ────────────────────────────────

def test_unconfigured_fallback_routes_to_openapi(monkeypatch):
    monkeypatch.delenv("PUSH_AGENT_WEBHOOK", raising=False)
    feishu_client._AGENT_WEBHOOK_CACHE["ts"] = 0.0
    feishu_client._AGENT_WEBHOOK_CACHE["url"] = ""
    monkeypatch.setenv(feishu_client.AGENT_WEBHOOK_FALLBACK_ENV, "1")
    called = {}

    def _fake_openapi(content, msg_type, receive_id, receive_id_type,
                      kind, flow_record):
        called["hit"] = True
        return True, "openapi_fallback: openapi direct ok", "om_x"

    monkeypatch.setattr(feishu_client, "_openapi_direct_send",
                        _fake_openapi)
    ok, why, mid = feishu_client._agent_webhook_send(
        "x", "text", "ou_x", "open_id", "test", False, None, False)
    assert called.get("hit") is True
    assert ok is True and mid == "om_x"


def test_unconfigured_no_fallback_rejects(monkeypatch):
    monkeypatch.delenv("PUSH_AGENT_WEBHOOK", raising=False)
    feishu_client._AGENT_WEBHOOK_CACHE["ts"] = 0.0
    feishu_client._AGENT_WEBHOOK_CACHE["url"] = ""
    monkeypatch.setenv(feishu_client.AGENT_WEBHOOK_FALLBACK_ENV, "0")
    ok, why, mid = feishu_client._agent_webhook_send(
        "x", "text", "ou_x", "open_id", "test", False, None, False)
    assert ok is False
    assert why.startswith("no endpoint configured")
    assert mid is None


# ── B4：ticker 意图 ──────────────────────────────────────────────

def test_ticker_bare_six_digits():
    assert inbound_gateway.classify("605100") == ("ticker", {"code": "605100"})
    assert inbound_gateway.classify("000001") == ("ticker", {"code": "000001"})


def test_confirm_semantics_outrank_ticker():
    assert inbound_gateway.classify("确认 605100")[0] == "confirm"


def test_short_text_not_misrouted():
    assert inbound_gateway.classify("hello") == ("unclear", {})
    assert inbound_gateway.classify("12345") == ("unclear", {})   # 5 位不认
    assert inbound_gateway.classify("1234567") == ("unclear", {})  # 7 位不认


def test_long_text_still_feed():
    long_text = "这是一段超过三十个字的纯文本内容，用来测试文本投喂" \
                "路由没有被 ticker 规则误伤，正常进入知识库收件箱。"
    assert inbound_gateway.classify(long_text)[0] == "text"


# ── N6：dead_letter.count_since（P0-4 死信观测） ─────────────────

def test_count_since_missing_file_returns_zero(monkeypatch, tmp_path):
    import dead_letter
    monkeypatch.setattr(dead_letter, "DEAD_PATH",
                        str(tmp_path / "nope.jsonl"))
    dead_letter._COUNT_CACHE.update({"ts": 0.0, "n": -1, "hours": 0})
    assert dead_letter.count_since(24) == 0  # 缺文件=合法空态


def test_count_since_counts_recent_rows_only(monkeypatch, tmp_path):
    import dead_letter
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(hours=1)).isoformat(
        timespec="seconds")
    stale = (datetime.now() - timedelta(hours=30)).isoformat(
        timespec="seconds")
    p = tmp_path / "dl.jsonl"
    p.write_text("\n".join([
        json.dumps({"ts": recent, "dead_id": "a"}),
        json.dumps({"ts": stale, "dead_id": "b"}),
        "not-json-line",
        json.dumps({"no_ts": True}),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(dead_letter, "DEAD_PATH", str(p))
    dead_letter._COUNT_CACHE.update({"ts": 0.0, "n": -1, "hours": 0})
    assert dead_letter.count_since(24) == 1  # 损坏/缺ts行跳过不计


def test_count_since_error_returns_minus_one(monkeypatch, tmp_path):
    import dead_letter
    monkeypatch.setattr(dead_letter, "DEAD_PATH", str(tmp_path))  # 目录→OSError
    dead_letter._COUNT_CACHE.update({"ts": 0.0, "n": -1, "hours": 0})
    assert dead_letter.count_since(24) == -1  # 读取异常不猜数
