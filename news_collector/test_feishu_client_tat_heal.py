# -*- coding: utf-8 -*-
"""K-ORG-199：feishu_client HTTP 4xx 包裹 TAT 失效码自愈缺口单测。

背景（2026-10-19 试发排障实证）：hub 进程持坏 TAT，飞书对失效令牌回
HTTP 400 包 {"code":99991663,...}，走 send_msg 的 HTTPError 分支原只读
详情不刷新缓存，只能干等 TTL（最长 6600s）自然过期。本组用例锁死：
400 包失效码 → 清缓存强刷重发一次；非失效码/非 JSON → 原路径不动；
自愈后重发仍失败 → 受控返回不炸。"""

import io
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feishu_client as client


class _Resp:
    """_post 成功路径的 context-manager 响应。"""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._b


def _http400(body_bytes):
    def raiser(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(body_bytes)
        )

    return raiser


def test_http400_wrapped_tat_invalid_self_heals(monkeypatch):
    """400 包 99991663 → 清缓存 + 强刷 + 重发一次成功（K-ORG-199 主场景）。"""
    monkeypatch.setattr(
        client, "_TAT_CACHE", {("app", "sec"): (time.time() + 999, "stale-token")}
    )
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    monkeypatch.setattr(client, "load_app_creds", lambda: ("app", "sec"))
    tat_calls, toks = [], ["tok-1", "tok-2"]

    def fake_tat(*a, **kw):
        tat_calls.append(kw.get("use_cache", True))
        return toks.pop(0)

    monkeypatch.setattr(client, "get_tat", fake_tat)
    posts = []

    def urlopen_mock(req, timeout=0):
        posts.append(req.get_header("Authorization"))
        if posts[-1] == "Bearer tok-1":  # 旧坏令牌 → 400 包失效码
            return _http400(
                json.dumps({"code": 99991663, "msg": "Invalid access token"}).encode(
                    "utf-8"
                )
            )(req, timeout)
        return _Resp({"code": 0, "msg": "success"})  # 新令牌 → 成功

    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen_mock)
    ok, why = client.send_msg(
        "sample", "text", "ou_mock", app_id="app", app_secret="sec", via_hub=False
    )
    assert ok is True and "code=0" in why
    assert posts == ["Bearer tok-1", "Bearer tok-2"]  # 原发 + 自愈重发
    assert tat_calls == [True, False]  # 第二次强制刷新
    assert ("app", "sec") not in client._TAT_CACHE  # 坏缓存已被清


def test_http400_non_tat_code_no_self_heal(monkeypatch):
    """400 包非失效码（如 99991672 参数错）→ 不刷新 token，原样报错。"""
    monkeypatch.setattr(client, "_TAT_CACHE", {})
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    state = {"n": 0}

    def tat_spy(*a, **kw):
        state["n"] += 1
        if state["n"] > 1 or kw.get("use_cache") is False:
            pytest.fail("非 TAT 失效码不应触发强刷")
        return "tok-x"

    monkeypatch.setattr(client, "get_tat", tat_spy)
    body = json.dumps({"code": 99991672, "msg": "param invalid"}).encode()
    monkeypatch.setattr(client.urllib.request, "urlopen", _http400(body))
    ok, why = client.send_msg(
        "sample", "text", "ou_mock", app_id="app", app_secret="sec", via_hub=False
    )
    assert ok is False and "99991672" in why
    assert state["n"] == 1


def test_http400_non_json_body_keeps_legacy_path(monkeypatch):
    """400 包非 JSON 响应体 → 解析降级，走原错误路径不炸。"""
    monkeypatch.setattr(client, "_TAT_CACHE", {})
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: "tok-x")
    monkeypatch.setattr(
        client.urllib.request, "urlopen", _http400(b"<html>gateway error</html>")
    )
    ok, why = client.send_msg(
        "sample", "text", "ou_mock", app_id="app", app_secret="sec", via_hub=False
    )
    assert ok is False and "openapi err" in why


def test_self_heal_retry_failure_is_contained(monkeypatch):
    """自愈重发仍失败（如网络断）→ 受控返回 err，不递归不抛出。"""
    monkeypatch.setattr(client, "_TAT_CACHE", {})
    monkeypatch.setattr(client, "_flow_record", lambda *a, **kw: None)
    monkeypatch.setattr(client, "load_app_creds", lambda: ("app", "sec"))
    monkeypatch.setattr(client, "get_tat", lambda *a, **kw: "tok-2")
    calls = {"n": 0}

    def urlopen_mock(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return _http400(
                json.dumps({"code": 99991663, "msg": "Invalid access token"}).encode(
                    "utf-8"
                )
            )(req, timeout)
        raise OSError("conn reset")  # 重发阶段网络故障

    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen_mock)
    ok, why = client.send_msg(
        "sample", "text", "ou_mock", app_id="app", app_secret="sec", via_hub=False
    )
    assert ok is False and "err" in why
    assert calls["n"] == 2  # 原发 + 重发各一次，无递归
