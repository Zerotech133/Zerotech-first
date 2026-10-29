# -*- coding: utf-8 -*-
"""service_restart 假成功回归测试。"""
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "service_restart.py"


def _load():
    spec = importlib.util.spec_from_file_location("service_restart_health", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_restart_never_returns_ok_with_failed_health(tmp_path):
    """旧实现只要求'曾经健康'，服务刚被杀/未拉起时会返回 ok=true + health=失败。"""
    m = _load()
    flag = tmp_path / "flag"
    calls = {"n": 0}

    def starter(task):
        return {"ok": True, "rc": 0}

    def health_fn(url):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return {"ok": True}          # 旧进程还活着
        if n == 2:
            flag.unlink(missing_ok=True)  # 任务消费 flag，同时服务已被杀
            return {"ok": False}
        return {"ok": True}              # 新进程已拉起

    rep = m.restart(flag, apply=True, starter=starter, health_fn=health_fn,
                    timeout=30, sleep=lambda s: None)
    assert rep["ok"] is True
    assert rep["restarted"] is True
    assert rep["health"]["ok"] is True, "不得返回假成功（health=失败却 ok=true）"


def test_restart_timeout_when_service_never_recovers(tmp_path):
    m = _load()
    flag = tmp_path / "flag"

    def starter(task):
        flag.unlink(missing_ok=True)
        return {"ok": True, "rc": 0}

    rep = m.restart(flag, apply=True, starter=starter,
                    health_fn=lambda u: {"ok": False}, timeout=6, sleep=lambda s: None)
    assert rep["ok"] is False and rep["error"] == "timeout"


def test_probe_url_with_custom_base():
    m = _load()
    assert m.probe_url("http://127.0.0.1:8796/api/zhaoyun", "/api/hq/meeting/stats") == \
        "http://127.0.0.1:8796/api/hq/meeting/stats"
    assert m.probe_url("http://127.0.0.1:8899/health", "/api/x") == "http://127.0.0.1:8899/api/x"
    assert m.probe_url("http://127.0.0.1:8899/health", "http://other/api") == "http://other/api"
