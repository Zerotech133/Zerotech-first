# -*- coding: utf-8 -*-
"""diagnostics 8787 探针统一单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。

验证：health.json 的 hermes_8787 走登录门感知探针；探针异常软降级为 error；
collect() 确实挂载 hermes_8787（不依赖真实服务）。
"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("diagnostics_hermes", HERE / "diagnostics.py")
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


def test_probe_hermes_auth_gated(monkeypatch):
    monkeypatch.setattr(
        diag, "_hermes_probe_fn",
        lambda: (lambda base, timeout=3: {"alive": True, "auth_gated": True,
                                          "status": 302, "error": ""}))
    r = diag.probe_hermes()
    assert r["alive"] is True and r["auth_gated"] is True
    assert r["status"] == 302 and r["url"].endswith("8787")


def test_probe_hermes_down_soft_degrade(monkeypatch):
    def boom():
        raise ImportError("hermes_health unavailable")

    monkeypatch.setattr(diag, "_hermes_probe_fn", boom)
    r = diag.probe_hermes()
    assert r["alive"] is False and r["status"] is None
    assert "ImportError" in r["error"]


def test_collect_wires_hermes_probe(monkeypatch):
    monkeypatch.setattr(diag, "probe_hermes",
                        lambda: {"url": "http://127.0.0.1:8787", "status": 401,
                                 "ms": 1.0, "alive": True, "auth_gated": True,
                                 "error": ""})
    monkeypatch.setattr(diag, "probe_port", lambda p: {"port": p, "up": False})
    monkeypatch.setattr(diag, "probe_http", lambda u: {"url": u, "status": 200})
    monkeypatch.setattr(diag, "_git", lambda args: "")
    monkeypatch.setattr(diag, "registry_count", lambda: 1)
    monkeypatch.setattr(diag, "latest_e2e", lambda: None)
    monkeypatch.setattr(diag, "latest_night", lambda: None)

    class _P:
        returncode = 0
        stdout = '{"ok": true}'

    monkeypatch.setattr(diag.subprocess, "run", lambda *a, **k: _P())
    payload = diag.collect(with_logs=False)
    entry = payload["health.json"]["hermes_8787"]
    assert entry["auth_gated"] is True and entry["alive"] is True
    assert "hermes_8787" not in diag.HEALTH_URLS  # 已从裸 URL 表移出
