# -*- coding: utf-8 -*-
"""feishu_console（邓芝台 W1 离线版）单测：数据采集结构、检查项、HTML 渲染、JSON 输出。"""
import json
import os
import tempfile
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_console as fc  # noqa: E402

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


def test_collect_structure():
    d = fc.collect()
    assert d["bots"], "至少应有注册表里的机器人"
    roles = {b["role"] for b in d["bots"]}
    assert "zb.owner.exec" in roles and "zb.ops.notify" in roles
    assert isinstance(d["routes"], list) and len(d["routes"]) >= 5
    assert "gateway" in d and "checks" in d
    for b in d["bots"]:
        for key in ("role", "display", "domain", "status", "secret_ready",
                    "group_mode", "events", "heartbeat_ok"):
            assert key in b
    # W3-D：收件人域登记面板
    assert isinstance(d["targets"], list) and d["targets"]
    for t in d["targets"]:
        assert set(t) >= {"name", "type", "id", "app"}
        assert t["app"], "push_targets 每条应有 app 字段（域登记）"
    assert d["canonical_app"], "canonical app 应可读"


def test_checks_have_channel_and_whitelist():
    d = fc.collect()
    names = [c for c, _ in d["checks"]]
    assert any("通道铁律" in n for n in names)
    assert any("群白名单" in n for n in names)
    assert any("收件人域登记" in n for n in names)
    assert any("收件人 app 与当前凭据一致" in n for n in names)


def test_render_html_contains_sections():
    d = fc.collect()
    html = fc.render_html(d)
    for seg in ("机器人台账", "一致性检查", "通道闸门", "路由表", "零界·执行"):
        assert seg in html


def test_json_cli():
    py = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_console.py")
    r = subprocess.run([py, script, "--json"], capture_output=True)
    assert r.returncode == 0
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["gateway"]["push_24h"] >= 0


def test_html_cli_writes_file(ws_tmp):
    py = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_console.py")
    out = os.path.join(ws_tmp, "console.html")
    r = subprocess.run([py, script, "--out", out], capture_output=True)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(out) and os.path.getsize(out) > 1000
