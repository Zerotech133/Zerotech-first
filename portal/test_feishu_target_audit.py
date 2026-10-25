# -*- coding: utf-8 -*-
"""feishu_target_audit 单测：分类、注入 opener 的校验、汇总口径（离线，不触网）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_target_audit as fta  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_classify():
    assert fta.classify_target({"id": "ou_abc"}) == "open_id"
    assert fta.classify_target({"id": "oc_xyz"}) == "chat_id"
    assert fta.classify_target({"id": "om_msg"}) == "unknown"
    assert fta.classify_target({}) == "unknown"


def test_audit_success_with_fake_opener():
    calls = []

    def opener(req, timeout=15):
        calls.append(req.full_url)
        return _Resp({"code": 0, "msg": "success", "data": {}})

    rows = fta.audit("tok", targets=[
        {"name": "个人", "id": "ou_1"},
        {"name": "群", "id": "oc_2"},
    ], opener=opener)
    assert rows["total"] == 2 and rows["ok"] == 2 and rows["bad"] == 0
    assert "contact/v3/users/ou_1" in calls[0]
    assert "im/v1/chats/oc_2" in calls[1]


def test_audit_business_error_marks_bad():
    def opener(req, timeout=15):
        return _Resp({"code": 230001, "msg": "invalid receive_id"})

    rows = fta.audit("tok", targets=[{"name": "群", "id": "oc_2"}],
                     opener=opener)
    assert rows["bad"] == 1
    assert "230001" in rows["rows"][0]["reason"]


def test_audit_unknown_prefix_and_no_token():
    rows = fta.audit("", targets=[
        {"name": "神秘", "id": "om_x"},
        {"name": "个人", "id": "ou_1"},
    ])
    assert rows["bad"] == 2
    assert "前缀" in rows["rows"][0]["reason"]
    assert "tenant token" in rows["rows"][1]["reason"]


def test_audit_app_mismatch_fails_fast_without_network():
    called = {"n": 0}

    def opener(req, timeout=15):
        called["n"] += 1
        return _Resp({"code": 0, "msg": "success"})

    rows = fta.audit("tok", targets=[
        {"name": "个人", "id": "ou_1", "app": "cli_A"}],
        opener=opener, canonical_app="cli_B")
    assert rows["bad"] == 1
    assert "app mismatch" in rows["rows"][0]["reason"]
    assert called["n"] == 0, "域不匹配应快速失败，不发网络请求"


def test_audit_app_match_passes_and_records_app():
    def opener(req, timeout=15):
        return _Resp({"code": 0, "msg": "success"})

    rows = fta.audit("tok", targets=[
        {"name": "量化群", "id": "oc_2", "app": "cli_A"}],
        opener=opener, canonical_app="cli_A")
    assert rows["ok"] == 1
    assert rows["rows"][0]["app"] == "cli_A"
    assert rows["canonical_app"] == "cli_A"
