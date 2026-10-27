# -*- coding: utf-8 -*-
"""量化只读 接口/页面 单测（runner 注入，不联网）。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_TOOLS = Path(__file__).resolve().parents[2] / "agent_platform"
sys.path.insert(0, str(_TOOLS))


def _load():
    from legion_command import zhaoyun_quant as zq
    return zq


def _fake_runner(payload):
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload),
                               stderr="")
    return run


def test_quant_view_ok():
    zq = _load()
    rep = zq.quant_view("stock_basic", limit=3,
                        runner=_fake_runner({"ok": True, "tool": "stock_basic",
                                             "count": 1, "rows": [{"a": 1}],
                                             "read_only": True}))
    assert rep["ok"] is True and rep["view"] == "quant_readonly"
    assert rep["count"] == 1


def test_quant_view_rejects_tool():
    zq = _load()
    rep = zq.quant_view("drop_table")
    assert rep["ok"] is False and rep["error"] == "tool_not_allowed"


def test_quant_view_runner_error_visible():
    zq = _load()

    def boom(cmd, **kwargs):
        raise OSError("spawn failed")
    rep = zq.quant_view("stock_basic", runner=boom)
    assert rep["ok"] is False and rep["error"] == "quant_call_failed"


def test_quant_view_bad_json_visible():
    zq = _load()

    def bad(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json", stderr="")
    rep = zq.quant_view("stock_basic", runner=bad)
    assert rep["ok"] is False and rep["error"] == "bad_json"


def test_page_contains_quant_section():
    html = (Path(__file__).resolve().parents[2] / "agent_platform"
            / "legion_command" / "static" / "zhaoyun.html").read_text(
                encoding="utf-8")
    assert "量化只读" in html
    assert "quantQuery" in html
    assert "/api/zhaoyun/quant" in html


def test_serve_has_quant_route():
    src = (Path(__file__).resolve().parents[2] / "agent_platform"
           / "legion_command" / "serve.py").read_text(encoding="utf-8")
    assert 'sub == "quant"' in src
