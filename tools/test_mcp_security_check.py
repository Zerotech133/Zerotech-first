# -*- coding: utf-8 -*-
"""D4：MCP 安全自查工具单测。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mcp_check", HERE / "mcp_security_check.py")
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


def _cfg(servers):
    return {"schema": "test/v1", "servers": servers}


def test_good_config_passes():
    cfg = _cfg({
        "fs": {"risk": "read-only", "script": "x.py", "allow_tools": ["a"]},
        "tushare": {"risk": "read-only", "transport": "http",
                    "url": "https://api.example.com/mcp/",
                    "token_env": "TUSHARE_TOKEN", "enabled": True},
    })
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is True
    assert r["errors"] == 0


def test_write_risk_fails():
    cfg = _cfg({"danger": {"risk": "write", "script": "x.py"}})
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is False
    assert any(f["check"] == "risk-read-only" and not f["ok"] for f in r["findings"])


def test_http_token_in_url_fails():
    cfg = _cfg({"t": {"risk": "read-only", "transport": "http",
                      "url": "https://api.example.com/mcp/?token=abc",
                      "token_env": "TOKEN_X", "enabled": True}})
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is False
    assert any(f["check"] == "http-url-no-token" and not f["ok"] for f in r["findings"])


def test_http_enabled_without_token_env_fails():
    cfg = _cfg({"t": {"risk": "read-only", "transport": "http",
                      "url": "https://api.example.com/mcp/", "enabled": True}})
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is False


def test_plaintext_secret_fails():
    cfg = _cfg({"t": {"risk": "read-only", "script": "x.py",
                      "note": "sk-abcdefghijklmnopqrstuvwxyz012345"}})
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is False
    assert any(f["check"] == "no-plaintext-secret" and not f["ok"] for f in r["findings"])


def test_allow_tools_star_is_warning_not_error():
    cfg = _cfg({"t": {"risk": "read-only", "script": "x.py", "allow_tools": "*"}})
    r = mcp.summarize(mcp.check_servers(cfg))
    assert r["ok"] is True
    assert r["warnings"] >= 1
