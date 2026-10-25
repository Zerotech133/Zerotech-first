# -*- coding: utf-8 -*-
"""W3 前置检查单测：结论口径、凭据判定、渲染、CLI。"""
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_w3_preflight as pre  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(r"C:\项目\work\_dsh_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_secret_ready(ws_tmp, monkeypatch):
    monkeypatch.setattr(pre, "SECRETS", ws_tmp)
    assert pre._secret_ready("zb.owner.exec") is False
    with open(os.path.join(ws_tmp, "zb.owner.exec.json"), "w", encoding="utf-8") as f:
        json.dump({"app_id": "cli_x", "app_secret": "s"}, f)
    assert pre._secret_ready("zb.owner.exec") is True
    with open(os.path.join(ws_tmp, "zb.owner.exec.json"), "w", encoding="utf-8") as f:
        json.dump({"app_id": "cli_x"}, f)
    assert pre._secret_ready("zb.owner.exec") is False


def test_preflight_structure():
    rep = pre.preflight()
    assert set(rep) >= {"ts", "verdict", "infra_go", "w2_go",
                        "w2_missing_roles", "acceptance", "rollback"}
    assert rep["verdict"] in ("GO", "PARTIAL-GO", "NO-GO")
    assert isinstance(rep["w2_missing_roles"], list)


def test_render_contains_verdict():
    rep = pre.preflight()
    md = pre.render(rep)
    assert "前置检查" in md and rep["verdict"] in md
    assert "W2" in md


def test_cli_json():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_w3_preflight.py")
    r = subprocess.run([sys.executable, script, "--json"],
                       capture_output=True, timeout=300)
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["verdict"] in ("GO", "PARTIAL-GO", "NO-GO")
