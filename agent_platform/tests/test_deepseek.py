"""DeepSeek 客户端离线测试（WO-LEGION-LLMDEEPSEEK-20261026）。

全部走注入 transport，不联网、不读真实密钥；密钥用拼接构造，避免扫描器误判。
覆盖：请求形状（URL/鉴权头/模型/消息）、响应解析、重试、错误脱敏、
密钥解析优先级（env > 显式文件 > KEY_FILE_ENV > 默认文件）、缺 key 报错。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import deepseek as D  # noqa: E402

_FAKE_KEY = "sk-" + "FAKEfake" + "0000"  # 拼接构造：不构成真实凭据


class _Capture:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers), "data": data,
                           "timeout": timeout})
        if self._responses:
            return self._responses.pop(0)
        return 200, json.dumps({"choices": [{"message": {"content": "OK"}}]})


def _client(capture, **kw):
    return D.DeepSeekClient(api_key=_FAKE_KEY, transport=capture,
                            sleep_fn=lambda s: None, **kw)


def test_chat_request_shape_and_parse():
    cap = _Capture()
    c = _client(cap)
    reply = c.chat([{"role": "user", "content": "hi"}], max_tokens=8)
    assert reply == "OK"
    call = cap.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer " + _FAKE_KEY
    payload = json.loads(call["data"].decode("utf-8"))
    assert payload["model"] == "deepseek-flash"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["stream"] is False


def test_models_endpoint():
    cap = _Capture([(200, json.dumps({"data": [{"id": "deepseek-flash"},
                                               {"id": "deepseek-v4-pro"}]}))])
    c = _client(cap)
    assert c.models() == ["deepseek-flash", "deepseek-v4-pro"]
    assert cap.calls[0]["method"] == "GET"
    assert cap.calls[0]["url"].endswith("/models")


def test_retry_on_503_then_success():
    cap = _Capture([(503, "busy"),
                    (200, json.dumps({"choices": [{"message": {"content": "OK"}}]}))])
    c = _client(cap, retries=1)
    assert c.chat([{"role": "user", "content": "hi"}]) == "OK"
    assert len(cap.calls) == 2


def test_error_is_sanitized_and_does_not_leak_key():
    cap = _Capture([(401, json.dumps({"error": {"message": "invalid key " + _FAKE_KEY}}))])
    c = _client(cap, retries=0)
    with pytest.raises(D.DeepSeekError) as ei:
        c.chat([{"role": "user", "content": "hi"}])
    msg = str(ei.value)
    assert _FAKE_KEY not in msg
    assert "HTTP 401" in msg


def test_bad_response_shape_raises():
    cap = _Capture([(200, json.dumps({"unexpected": True}))])
    c = _client(cap)
    with pytest.raises(D.DeepSeekError):
        c.chat([{"role": "user", "content": "hi"}])


def test_resolve_key_env_first(monkeypatch):
    monkeypatch.setenv(D.KEY_ENV, _FAKE_KEY)
    assert D.resolve_api_key() == _FAKE_KEY


def test_resolve_key_from_explicit_file(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text(_FAKE_KEY + "\n", encoding="utf-8")
    assert D.resolve_api_key(env={}, explicit_file=str(p)) == _FAKE_KEY


def test_resolve_key_from_env_file_path(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text(_FAKE_KEY, encoding="utf-8")
    assert D.resolve_api_key(env={D.KEY_FILE_ENV: str(p)}) == _FAKE_KEY


def test_resolve_key_missing_raises_honest_error(tmp_path, monkeypatch):
    monkeypatch.delenv(D.KEY_ENV, raising=False)
    missing = tmp_path / "nope.txt"
    with pytest.raises(D.DeepSeekError) as ei:
        D.resolve_api_key(env={}, explicit_file=str(missing))
    assert "缺少 DeepSeek key" in str(ei.value)
    assert _FAKE_KEY not in str(ei.value)


def test_ping_combines_models_and_chat(tmp_path):
    cap = _Capture([
        (200, json.dumps({"data": [{"id": "deepseek-flash"}]})),
        (200, json.dumps({"choices": [{"message": {"content": "OK"}}]})),
    ])
    out = _client(cap).ping()
    assert out["ok"] is True and out["models"] == ["deepseek-flash"]
    assert out["reply"] == "OK"
