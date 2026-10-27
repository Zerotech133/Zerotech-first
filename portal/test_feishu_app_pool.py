# -*- coding: utf-8 -*-
"""feishu_app_pool 单测（W2 前置）：角色凭据确定化、sender 选择、目标域校验、状态报告。"""
import json
import os
import tempfile
import shutil
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_app_pool as pool  # noqa: E402


@pytest.fixture()
def ws_tmp():
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_test_tmp", uuid.uuid4().hex)
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _put_secret(base, role, aid, sec="s3"):
    with open(os.path.join(base, role + ".json"), "w", encoding="utf-8") as f:
        json.dump({"app_id": aid, "app_secret": sec}, f)


def _put_canonical(base, aid):
    with open(os.path.join(base, "canonical.json"), "w", encoding="utf-8") as f:
        json.dump({"app_id": aid}, f)


def test_load_role_app_ok(ws_tmp):
    _put_secret(ws_tmp, "zb.owner.exec", "cli_exec")
    assert pool.load_role_app("zb.owner.exec", ws_tmp) == ("cli_exec", "s3")


def test_load_role_app_missing_raises(ws_tmp):
    with pytest.raises(pool.AppPoolError):
        pool.load_role_app("zb.no.such", ws_tmp)


def test_load_role_app_incomplete_raises(ws_tmp):
    with open(os.path.join(ws_tmp, "zb.bad.json"), "w", encoding="utf-8") as f:
        json.dump({"app_id": "cli_x"}, f)
    with pytest.raises(pool.AppPoolError):
        pool.load_role_app("zb.bad", ws_tmp)


def test_canonical_from_file_and_env(ws_tmp, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    _put_canonical(ws_tmp, "cli_canon")
    assert pool.canonical_app_id(ws_tmp) == "cli_canon"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    assert pool.canonical_app_id(ws_tmp) == "cli_env"


def test_app_for_role_caches_and_invalidates(ws_tmp):
    _put_secret(ws_tmp, "zb.owner.exec", "cli_v1")
    assert pool.app_for_role("zb.owner.exec", ws_tmp)[0] == "cli_v1"
    import time
    time.sleep(0.01)
    _put_secret(ws_tmp, "zb.owner.exec", "cli_v2")
    # mtime 变化 → 重新加载
    assert pool.app_for_role("zb.owner.exec", ws_tmp)[0] == "cli_v2"


def test_resolve_sender_prefers_role_secret(ws_tmp, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    _put_canonical(ws_tmp, "cli_canon")
    _put_secret(ws_tmp, "zb.ops.notify", "cli_notify")
    assert pool.resolve_sender_app("owner", "zb.ops.notify", ws_tmp) == "cli_notify"


def test_resolve_sender_falls_back_to_canonical(ws_tmp, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    _put_canonical(ws_tmp, "cli_canon")
    assert pool.resolve_sender_app("owner", "zb.owner.exec", ws_tmp) == "cli_canon"


def test_resolve_sender_customer_requires_own_app(ws_tmp, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    with pytest.raises(pool.AppPoolError):
        pool.resolve_sender_app("customer", "zb.customer.svc", ws_tmp)


def test_validate_target_app():
    ok, why = pool.validate_target_app({"app": "cli_A"}, "cli_A")
    assert ok and why == "ok"
    ok, why = pool.validate_target_app({"app": "cli_A"}, "cli_B")
    assert not ok and "mismatch" in why
    ok, why = pool.validate_target_app({"app": ""}, "cli_B")
    assert ok and "未登记" in why
    ok, why = pool.validate_target_app({"app": "cli_A"}, "")
    assert not ok


def test_status_report_reads_real_registry(ws_tmp, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_canon")
    rep = pool.status_report(ws_tmp)
    assert rep["canonical_app"] == "cli_canon"
    roles = {r["role"] for r in rep["roles"]}
    assert "zb.owner.exec" in roles and "zb.ops.notify" in roles
    # 无 secrets 时全部 secret_ready False（计划中角色）
    assert all(r["secret_ready"] is False for r in rep["roles"])


def test_cli_runs(monkeypatch):
    import subprocess
    py = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_app_pool.py")
    r = subprocess.run([py, script], capture_output=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert "canonical_app" in data and isinstance(data["roles"], list)
