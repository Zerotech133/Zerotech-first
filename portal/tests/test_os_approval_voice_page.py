# -*- coding: utf-8 -*-
"""语音审批台页面结构测试（纯静态页，不改服务）。"""

import os
import re

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(PORTAL, "os_approval_voice.html")


def test_page_exists_and_has_shell():
    assert os.path.isfile(PAGE)
    text = open(PAGE, encoding="utf-8").read()
    for need in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js"):
        assert need in text, need


def test_page_uses_local_asr_and_flow_apis():
    text = open(PAGE, encoding="utf-8").read()
    for need in (
        "/api/os/asr",
        "/api/os/consumer/register",
        "/approval/request",
        "/approval/decide",
        "/l2/outbox",
        "/chain/c1",
        "/approvals",
        "X-OS-Page",
    ):
        assert need in text, need


def test_page_has_voice_guides_and_confirm():
    text = open(PAGE, encoding="utf-8").read()
    for need in (
        "载入事项",
        "注册消费者",
        "申请发布审批",
        "批准",
        "确认执行",
        "记录 outbox",
        "看链",
        "CONTROLLED_L2_APPROVED",
    ):
        assert need in text, need
    assert "speechSynthesis" in text and "mediaDevices" in text


def test_page_js_syntax_balanced():
    text = open(PAGE, encoding="utf-8").read()
    m = re.findall(r"<script>(.*?)</script>", text, re.S)
    js = m[-1]
    for a, b in (("(", ")"), ("{", "}"), ("[", "]")):
        assert js.count(a) == js.count(b), (a, js.count(a), js.count(b))
