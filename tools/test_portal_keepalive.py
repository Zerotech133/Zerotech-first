# -*- coding: utf-8 -*-
"""门户族保活脚本测试（2026-11-16 WO-PORTAL-KEEPALIVE）。

只测纯函数与错误路径，不真起服务、不碰生产端口。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    path = REPO / "tools" / "portal_keepalive.py"
    spec = importlib.util.spec_from_file_location("portal_keepalive_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ka = _load()


def test_listening_false_on_closed_port():
    # 高位端口既没监听也不该抛错
    assert ka.listening(59631) is False


def test_listening_true_on_live_port():
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert ka.listening(port) is True
    finally:
        srv.close()


def test_http_probe_none_on_unreachable():
    status, err = ka.http_probe("http://127.0.0.1:59632/health", timeout=0.5)
    assert status is None and err


def test_http_probe_none_when_url_empty():
    assert ka.http_probe(None) == (None, "")


def test_owner_pid_returns_none_or_int():
    pid = ka.owner_pid(59633)
    assert pid is None or isinstance(pid, int)


def test_dry_run_reports_would_start_for_existing_script(tmp_path):
    """脚本存在时 dry-run 只报「将会启动」，不真起进程。"""
    fake = tmp_path / "fake_service.py"
    fake.write_text("raise SystemExit(0)\n", encoding="utf-8")
    svc = {"name": "fake", "port": 59634, "script": "fake_service.py",
           "workdir": tmp_path, "health": None, "critical": False}
    row = ka.check_one(svc, sys.executable, dry_run=True)
    assert row["action"] == "dry_run_would_start"
    assert row["ok"] is True
    assert row["would_start"].endswith("fake_service.py")
    assert ka.listening(59634) is False  # 没真起


def test_dry_run_missing_script_is_visible(tmp_path):
    """脚本缺失时 dry-run 也如实报错，不假装会成功。"""
    svc = {"name": "ghost", "port": 59635, "script": "no_such_service.py",
           "workdir": tmp_path, "health": None, "critical": False}
    row = ka.check_one(svc, sys.executable, dry_run=True)
    assert row["action"] == "start_failed"
    assert row["ok"] is False
    assert "script_missing" in row["error"]


def test_start_failure_is_visible(tmp_path):
    svc = {"name": "ghost2", "port": 59636, "script": "no_such_service.py",
           "workdir": tmp_path, "health": None, "critical": False}
    row = ka.check_one(svc, sys.executable, dry_run=False)
    assert row["action"] == "start_failed"
    assert row["ok"] is False
    assert "script_missing" in row["error"]


def test_services_table_shape():
    ports = {s["port"] for s in ka.SERVICES}
    assert {8091, 8098, 8789} <= ports
    for svc in ka.SERVICES:
        assert svc["script"].endswith(".py")
        assert svc["workdir"].is_dir()
