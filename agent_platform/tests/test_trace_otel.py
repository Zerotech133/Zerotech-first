# -*- coding: utf-8 -*-
"""OTel→Phoenix 导出单测（fake urlopen，不联网）。"""
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM.parent))


def _load():
    from observability import trace
    return trace


def test_otel_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ZHAOYUN_OTEL", raising=False)
    t = _load()
    rep = t.record("a" * 32, "test", db=tmp_path / "t.sqlite")
    assert rep["ok"] is True
    assert rep["otel"]["skipped"] == "otel_off"


def test_otel_export_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("ZHAOYUN_OTEL", "1")
    monkeypatch.setenv("ZHAOYUN_OTEL_ENDPOINT", "http://127.0.0.1:4318")
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _Resp()

    # safe_urlopen 对回环地址走独立 opener，必须 patch trace 模块实际调用的符号，
    # 而不是 urllib.request.urlopen；否则测试会绕过 fake 访问 127.0.0.1:4318。
    t = _load()
    monkeypatch.setattr(t, "safe_urlopen", fake_urlopen)
    rep = t.record("b" * 32, "cap.test", kind="tool", status="ok",
                   attrs={"capability": "selftest"}, db=tmp_path / "t.sqlite")
    assert rep["otel"]["ok"] is True
    assert captured["url"].endswith("/v1/traces")
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray)) and body[:1] == b"\x0a"
    assert b"cap.test" in body and b"capability" in body
    assert bytes.fromhex("b" * 32) in body


def test_otel_error_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("ZHAOYUN_OTEL", "1")
    monkeypatch.setenv("ZHAOYUN_OTEL_ENDPOINT", "http://127.0.0.1:4318")

    def boom(req, timeout=0):
        raise OSError("phoenix down")

    t = _load()
    monkeypatch.setattr(t, "safe_urlopen", boom)
    rep = t.record("c" * 32, "test", db=tmp_path / "t.sqlite")
    assert rep["ok"] is True and rep["otel"]["ok"] is False
