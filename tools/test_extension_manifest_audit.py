# -*- coding: utf-8 -*-
"""O3-P2 对账工具单测。"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("extension_manifest_audit", HERE / "extension_manifest_audit.py")
ema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ema)


def _server(**kw):
    s = {"script": "x.py", "risk": "read-only", "allow_tools": ["a"], "transport": "stdio"}
    s.update(kw)
    return s


def _manifest(**kw):
    m = {"name": "x", "risk": "read-only", "allow_tools": ["a"], "transport": "stdio",
         "runtime": {"mcp_server": "x"}}
    m.update(kw)
    return m


def test_compare_match():
    assert ema.compare_mcp(_manifest(), _server()) == []


def test_compare_risk_mismatch():
    f = ema.compare_mcp(_manifest(risk="read-only"), _server(risk="write"))
    assert any(x[0] == "risk" for x in f)


def test_compare_allow_mismatch():
    f = ema.compare_mcp(_manifest(allow_tools=["a", "b"]), _server(allow_tools=["a"]))
    assert any(x[0] == "allow_tools" for x in f)


def test_compare_http_token():
    m = _manifest(transport="http", token_env="TOKEN_A", url="https://x/mcp/")
    s = _server(transport="http", token_env="TOKEN_B", url="https://x/mcp/")
    f = ema.compare_mcp(m, s)
    assert any(x[0] == "token_env" for x in f)


def test_audit_missing_server(tmp_path):
    ext = tmp_path / "ext" / "demo"
    ext.mkdir(parents=True)
    (ext / "manifest.json").write_text(json.dumps(_manifest(runtime={"mcp_server": "nope"})),
                                       encoding="utf-8")
    srv = tmp_path / "servers.json"
    srv.write_text(json.dumps({"servers": {}}), encoding="utf-8")
    r = ema.audit(str(tmp_path / "ext"), str(srv))
    assert r["ok"] is False
    assert r["mismatches"][0]["field"] == "missing_server"


def test_audit_unmigrated_warn(tmp_path):
    ext = tmp_path / "ext" / "demo"
    ext.mkdir(parents=True)
    (ext / "manifest.json").write_text(json.dumps(_manifest(runtime={"mcp_server": "x"})),
                                       encoding="utf-8")
    srv = tmp_path / "servers.json"
    srv.write_text(json.dumps({"servers": {"x": _server(), "y": _server()}}), encoding="utf-8")
    r = ema.audit(str(tmp_path / "ext"), str(srv))
    assert r["ok"] is True
    assert r["unmigrated_servers"] == ["y"]
