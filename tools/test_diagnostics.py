# -*- coding: utf-8 -*-
"""O7 diagnostics 单测（不依赖真实服务）。"""
import importlib.util
import json
import pathlib
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("diagnostics", HERE / "diagnostics.py")
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


def test_redact_token_kv():
    s = diag.redact_text("token=abc123456 foo api_key: zzz999")
    assert "abc123456" not in s and "zzz999" not in s
    assert "<redacted>" in s
    assert "foo" in s


def test_redact_common_shapes():
    s = diag.redact_text("sk-abcdefghijklmnop ghp_ABCDEFGHIJKLMNOPQRST xoxb-1234567890 Bearer abc.def-ghi")
    for bad in ("sk-abcdefghijklmnop", "ghp_ABCDEFGHIJKLMNOPQRST", "xoxb-1234567890", "abc.def-ghi"):
        assert bad not in s
    assert s.count("<redacted>") >= 4


def test_redact_keeps_normal_text():
    s = diag.redact_text("8091 health 200; e2e passed 13")
    assert s == "8091 health 200; e2e passed 13"


def test_tail_file(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("\n".join("line%d" % i for i in range(500)), encoding="utf-8")
    t = diag.tail_file(str(p), lines=5)
    assert t.splitlines() == ["line495", "line496", "line497", "line498", "line499"]


def test_tail_file_missing():
    t = diag.tail_file("Z:\\no-such-file.log")
    assert t.startswith("<unavailable")


def test_write_zip(tmp_path):
    payload = {"meta.json": {"a": 1}, "README.txt": "hello", "logs/x.log": "line"}
    path = diag.write_zip(str(tmp_path), payload)
    assert path.endswith(".zip")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert names == {"meta.json", "README.txt", "logs/x.log"}
        assert json.loads(z.read("meta.json").decode("utf-8"))["a"] == 1


def test_collect_does_not_leak_env_value(monkeypatch):
    monkeypatch.setenv("TUSHARE_MCP_TOKEN", "super-secret-value-123")
    payload = diag.collect(with_logs=False)
    raw = json.dumps(payload, ensure_ascii=False)
    assert "super-secret-value-123" not in raw
    assert payload["meta.json"]["env_presence"]["TUSHARE_MCP_TOKEN"] is True
