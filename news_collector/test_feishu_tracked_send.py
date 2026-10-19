# -*- coding: utf-8 -*-
"""K-ORG-201 W38：feishu_client tracked 发送 / verify_message 单测（全 mock 零触网）。

锁定：
  - send_msg 签名与 (ok, why) 返回零感知（在役调用方兼容）；
  - send_msg_tracked 三元组：直发=data.message_id、hub=响应顶层 message_id、缺则 None；
  - verify_message 只读反查：code=0→True；404/其他码/异常→False；
  - push_flow.record evidence 键：合法落键 / None 不落键 / 非法醒目 dead_letter。
"""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):  # news_collector + 仓根（push_flow）
    if p not in sys.path:
        sys.path.insert(0, p)

import feishu_client as client  # noqa: E402
import push_flow  # noqa: E402


class _Resp:
    """context-manager 响应（urlopen mock 用）。"""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._b


@pytest.fixture
def direct_env(monkeypatch):
    """直发链路 mock：凭据/TAT/流水埋点全替，绝不触网。"""
    monkeypatch.setattr(client, "_TAT_CACHE", {})
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    monkeypatch.setattr(client, "load_app_creds", lambda: ("app", "sec"))
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: "tok-mock")
    return monkeypatch


# ---------- K-ORG-204：via_hub=False 已改为 Agent webhook 推送 ----------

def test_direct_send_msg_still_two_tuple(direct_env, monkeypatch):
    """K-ORG-204：via_hub=False 推 Agent webhook，不再调 OpenAPI。"""
    monkeypatch.setattr(client, "_agent_webhook",
                        lambda: "https://agent.local/webhook")
    def urlopen(req, timeout=0):
        assert req.full_url == "https://agent.local/webhook"
        return _Resp({"ok": True, "why": "agent ok",
                      "message_id": "om_agent1"})
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    ok, why = client.send_msg("s", "text", "ou_m", via_hub=False)
    assert ok is True and "agent ok" in why


def test_direct_send_msg_tracked_three_tuple(direct_env, monkeypatch):
    monkeypatch.setattr(client, "_agent_webhook",
                        lambda: "https://agent.local/webhook")
    def urlopen(req, timeout=0):
        return _Resp({"ok": True, "message_id": "om_agent2"})
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    ok, why, mid = client.send_msg_tracked("s", "text", "ou_m", via_hub=False)
    assert ok is True and mid == "om_agent2"


def test_direct_tracked_missing_message_id_is_none(direct_env, monkeypatch):
    monkeypatch.setattr(client, "_agent_webhook",
                        lambda: "https://agent.local/webhook")
    monkeypatch.setattr(client.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp({"ok": True}))
    ok, why, mid = client.send_msg_tracked("s", "text", "ou_m", via_hub=False)
    assert ok is True and mid is None            # Agent 无 message_id → None
    # Agent webhook 拒绝
    monkeypatch.setattr(client.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp({"ok": False, "error": "denied"}))
    ok2, why2, mid2 = client.send_msg_tracked("s", "text", "ou_m", via_hub=False)
    assert ok2 is False and mid2 is None         # 失败不出 mid


# ---------- hub 链路 message_id（闸门唯一通路，解析响应顶层） ----------

def test_hub_tracked_message_id_passthrough(monkeypatch):
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    def urlopen(req, timeout=0):
        assert req.full_url == client.HUB_SEND_URL
        return _Resp({"ok": True, "why": "hub ok", "message_id": "om_hub1"})
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    ok, why, mid = client.send_msg_tracked("s", "text", "oc_m", via_hub=True)
    assert ok is True and mid == "om_hub1"
    ok2, why2 = client.send_msg("s", "text", "oc_m")   # send_msg 仍二元组
    assert ok2 is True


def test_hub_tracked_without_message_id_is_none(monkeypatch):
    monkeypatch.setenv("PUSH_HUB_KEY", "hub-secret")
    monkeypatch.setattr(client.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp({"ok": True, "why": "hub ok"}))
    assert client.send_msg_tracked("s", "text", "oc_m")[2] is None
    monkeypatch.setattr(client.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp({"ok": False, "error": "silent window"}))
    ok, why, mid = client.send_msg_tracked("s", "text", "oc_m")
    assert ok is False and mid is None and "silent" in why


# ---------- verify_message：只读反查 fail-closed ----------

def test_verify_message_code_zero_true(direct_env, monkeypatch):
    def urlopen(req, timeout=0):
        assert req.full_url == client.OPEN + "/im/v1/messages/om_x"
        assert req.get_header("Authorization") == "Bearer tok-mock"
        return _Resp({"code": 0, "data": {"message_id": "om_x"}})
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    assert client.verify_message("om_x") is True


def test_verify_message_bad_code_404_exception_false(direct_env, monkeypatch):
    monkeypatch.setattr(client.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp({"code": 230001, "msg": "not exist"}))
    assert client.verify_message("om_x") is False   # 业务码非 0 → False

    def e404(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(client.urllib.request, "urlopen", e404)
    assert client.verify_message("om_x") is False   # 404 → False

    def boom(req, timeout=0):
        raise OSError("network down")
    monkeypatch.setattr(client.urllib.request, "urlopen", boom)
    assert client.verify_message("om_x") is False   # 异常 → False 不抛


def test_verify_message_no_creds_or_mid_false(direct_env, monkeypatch):
    monkeypatch.setattr(client, "load_app_creds", lambda: (None, None))
    assert client.verify_message("om_x") is False   # 凭证缺失 → False
    monkeypatch.setattr(client, "load_app_creds", lambda: ("app", "sec"))
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: None)
    assert client.verify_message("om_x") is False   # TAT 不可用 → False
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: "tok-mock")
    assert client.verify_message("") is False       # 空 mid → False


# ---------- push_flow.record evidence 键 ----------

def test_push_flow_record_evidence_key(monkeypatch, tmp_path):
    f = tmp_path / "flow.jsonl"
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(f))
    push_flow.record("k1", "openapi", "oc_a", 1, True, evidence="receiver_verified")
    push_flow.record("k2", "openapi", "oc_a", 1, True)
    push_flow.record("k3", "openapi", "oc_a", 1, True, evidence="bogus")
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["evidence"] == "receiver_verified"       # 合法值落键
    assert "evidence" not in rows[1]                        # None 不落键
    assert set(rows[1]) == {"ts", "date", "kind", "channel", "target",
                            "count", "ok", "tenant"}        # 行结构向后兼容
    assert rows[2]["evidence"] == "dead_letter"             # 非法值醒目
