# -*- coding: utf-8 -*-
"""W3 清债单测：凭据确定化（load_app_creds）、OpenAPI 直发重试与错误体证据。

离线运行：全部 HTTP 走注入的 feishu_client._HTTP_OPEN，不发真实请求。
"""
import json
import os
import tempfile
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_client as fc  # noqa: E402

import shutil
import uuid


@pytest.fixture()
def ws_tmp():
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── 凭据确定化 ───────────────────────────────────────────────
def _write_cfg(ws_tmp, apps):
    p = os.path.join(ws_tmp, "config.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"apps": apps}, ensure_ascii=False))
    return p


def test_canonical_picks_exact_app(monkeypatch, ws_tmp):
    cfg = _write_cfg(ws_tmp, [
        {"appId": "cli_new", "appSecret": "newsec"},
        {"appId": "cli_old", "appSecret": "oldsec"},
    ])
    monkeypatch.setenv("HOME", ws_tmp)
    monkeypatch.setenv("USERPROFILE", ws_tmp)
    monkeypatch.setenv("FEISHU_APP_ID", "cli_old")
    monkeypatch.setattr(fc, "_canonical_app_id", lambda: "cli_old")
    # 实际读路径用 expanduser 拼接，直接改 cfg 常量不可行 → 用 monkeypatch 打开函数
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path).endswith("config.json"):
            return real_open(cfg, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open, raising=False)
    aid, sec = fc.load_app_creds()
    assert (aid, sec) == ("cli_old", "oldsec")


def test_canonical_missing_app_fails_loud(monkeypatch, ws_tmp, capsys):
    cfg = _write_cfg(ws_tmp, [{"appId": "cli_a", "appSecret": "s"}])
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path).endswith("config.json"):
            return real_open(cfg, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open, raising=False)
    monkeypatch.setattr(fc, "_canonical_app_id", lambda: "cli_not_exist")
    aid, sec = fc.load_app_creds()
    assert (aid, sec) == (None, None)
    assert "不在 ~/.lark-cli/config.json" in capsys.readouterr().err


def test_env_direct_creds_win(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "envsec")
    assert fc.load_app_creds() == ("cli_env", "envsec")


def test_fallback_apps0_warns_once(monkeypatch, ws_tmp, capsys):
    cfg = _write_cfg(ws_tmp, [{"appId": "cli_first", "appSecret": "s1"},
                              {"appId": "cli_second", "appSecret": "s2"}])
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path).endswith("config.json"):
            return real_open(cfg, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open, raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.setattr(fc, "_canonical_app_id", lambda: "")
    fc._WARNED_APPS.clear()
    aid, sec = fc.load_app_creds()
    assert (aid, sec) == ("cli_first", "s1")
    err = capsys.readouterr().err
    assert "WARN" in err and "apps[0]" in err


# ── OpenAPI 直发重试 ─────────────────────────────────────────
class _Resp:
    def __init__(self, payload, status=200):
        self._b = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_http(monkeypatch, script):
    """script: 依次返回 _Resp / 抛 HTTPError 的函数列表；记录调用次数。"""
    calls = {"n": 0}
    seq = list(script)

    def opener(req, timeout=15):
        i = calls["n"]
        calls["n"] += 1
        item = seq[i] if i < len(seq) else seq[-1]
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(fc, "_HTTP_OPEN", opener)
    return calls


def _http_error(code, body):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode("utf-8")))


def test_retry_on_rate_limit_then_ok(monkeypatch):
    ok = _Resp({"code": 0, "msg": "success", "data": {"message_id": "om_1"}})
    calls = _fake_http(monkeypatch, [
        _http_error(400, '{"code":99991400,"msg":"rate limited"}'), ok])
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    okflag, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "ou_x", "open_id", kind=None, flow_record=False)
    assert okflag is True and mid == "om_1" and calls["n"] == 2


def test_no_retry_on_param_error_and_body_in_why(monkeypatch):
    calls = _fake_http(monkeypatch, [
        _http_error(400, '{"code":230001,"msg":"invalid receive_id"}')])
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    okflag, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "oc_bad", "chat_id", kind=None, flow_record=False)
    assert okflag is False and calls["n"] == 1
    assert "230001" in why and "invalid receive_id" in why


def test_token_invalid_refreshes_and_retries(monkeypatch):
    refresh = {"n": 0}

    def fake_tat(*a, **kw):
        if kw.get("use_cache") is False:
            refresh["n"] += 1
            return "tok2"
        return "tok1"

    monkeypatch.setattr(fc, "get_tat", fake_tat)
    calls = _fake_http(monkeypatch, [
        _Resp({"code": 99991663, "msg": "invalid token"}),
        _Resp({"code": 0, "msg": "ok", "data": {"message_id": "om_2"}})])
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    okflag, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "ou_x", "open_id", kind=None, flow_record=False)
    assert okflag is True and mid == "om_2"
    assert refresh["n"] == 1 and calls["n"] == 2


def test_server_error_exhausts_retries(monkeypatch):
    calls = _fake_http(monkeypatch, [_http_error(503, "{}")])
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    okflag, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "ou_x", "open_id", kind=None, flow_record=False)
    assert okflag is False and calls["n"] == 3


def test_biz_code_helper():
    assert fc._biz_code_from_body('{"code":230001}') == 230001
    assert fc._biz_code_from_body("not-json") is None


# ── 目标域登记校验（W3-D）────────────────────────────────────
def test_target_app_guard_lookup():
    targets = [{"name": "个人", "id": "ou_x", "app": "cli_A"},
               {"name": "老条目", "id": "ou_y"},
               {"name": "群", "id": "oc_g", "app": "cli_A"}]
    assert fc._target_app_guard("ou_x", targets=targets) == "cli_A"
    assert fc._target_app_guard("ou_y", targets=targets) is None
    assert fc._target_app_guard("ou_missing", targets=targets) is None
    # 2026-10-25 W3：chat_id 为租户级共享，不做域拦截
    assert fc._target_app_guard("oc_g", targets=targets) is None


def test_target_app_mismatch_fails_fast(monkeypatch):
    import types
    fake_pt = types.SimpleNamespace(
        targets=lambda: [{"name": "个人", "id": "ou_x", "app": "cli_A"}])
    monkeypatch.setitem(sys.modules, "push_targets", fake_pt)
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setattr(fc, "load_app_creds", lambda: ("cli_B", "sec"))
    called = {"n": 0}

    def opener(req, timeout=15):
        called["n"] += 1
        return _Resp({"code": 0})

    monkeypatch.setattr(fc, "_HTTP_OPEN", opener)
    ok, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "ou_x", "open_id",
        kind=None, flow_record=False)
    assert ok is False
    assert "target app mismatch" in why and "cli_A" in why and "cli_B" in why
    assert called["n"] == 0


# ── W3 按角色选 sender（sender_role）────────────────────────
def test_openapi_uses_sender_app_override(monkeypatch):
    import types
    seen = {}

    def fake_tat(app_id=None, app_secret=None, **kw):
        seen["app"] = (app_id, app_secret)
        return "tok_override"

    monkeypatch.setattr(fc, "get_tat", fake_tat)
    monkeypatch.setitem(sys.modules, "push_targets", types.SimpleNamespace(
        targets=lambda: [{"name": "群", "id": "oc_x", "app": "cli_NOTIFY"}]))
    monkeypatch.setattr(fc, "_HTTP_OPEN",
                        lambda req, timeout=15: _Resp(
                            {"code": 0, "data": {"message_id": "om_s"}}))
    ok, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "oc_x", "chat_id", kind=None,
        flow_record=False, app_id="cli_NOTIFY", app_secret="notifysec")
    assert ok is True and mid == "om_s"
    assert seen["app"] == ("cli_NOTIFY", "notifysec")


def test_openapi_guard_reports_override_mismatch(monkeypatch):
    import types
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setitem(sys.modules, "push_targets", types.SimpleNamespace(
        targets=lambda: [{"name": "个人", "id": "ou_x", "app": "cli_NOTIFY"}]))
    monkeypatch.setattr(fc, "_HTTP_OPEN",
                        lambda req, timeout=15: _Resp({"code": 0}))
    ok, why, _ = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "ou_x", "open_id", kind=None,
        flow_record=False, app_id="cli_OTHER", app_secret="s")
    assert ok is False and "cli_NOTIFY" in why and "cli_OTHER" in why


def test_openapi_chat_target_allows_cross_app(monkeypatch):
    """chat_id 租户级共享：登记 app 不同也允许发送（2026-10-25 W3 实证）。"""
    import types
    monkeypatch.setattr(fc, "get_tat", lambda *a, **kw: "tok")
    monkeypatch.setitem(sys.modules, "push_targets", types.SimpleNamespace(
        targets=lambda: [{"name": "群", "id": "oc_x", "app": "cli_OLD"}]))
    monkeypatch.setattr(fc, "_HTTP_OPEN",
                        lambda req, timeout=15: _Resp(
                            {"code": 0, "data": {"message_id": "om_ok"}}))
    ok, why, mid = fc._openapi_direct_send(
        '{"text":"hi"}', "text", "oc_x", "chat_id", kind=None,
        flow_record=False, app_id="cli_NEW", app_secret="s")
    assert ok is True and mid == "om_ok"


def test_hub_payload_carries_sender_role(monkeypatch):
    captured = {}

    class _HubResp:
        def read(self):
            return json.dumps({"ok": True, "message_id": "om_h"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=8):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _HubResp()

    monkeypatch.setattr(fc, "_hub_key", lambda: "k")
    monkeypatch.setattr(fc.urllib.request, "urlopen", fake_urlopen)
    ok, why, mid = fc._hub_send('{"text":"hi"}', "text", "ou_x", "open_id",
                                sender_role="zb.ops.notify")
    assert ok is True and mid == "om_h"
    assert captured["body"]["sender_role"] == "zb.ops.notify"


def test_hub_payload_omits_sender_role_by_default(monkeypatch):
    captured = {}

    class _HubResp:
        def read(self):
            return json.dumps({"ok": True}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=8):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _HubResp()

    monkeypatch.setattr(fc, "_hub_key", lambda: "k")
    monkeypatch.setattr(fc.urllib.request, "urlopen", fake_urlopen)
    fc._hub_send('{"text":"hi"}', "text", "ou_x", "open_id")
    assert "sender_role" not in captured["body"]
