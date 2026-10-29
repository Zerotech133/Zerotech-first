# -*- coding: utf-8 -*-
"""兵棋接线单测：视图函数 + 页面/路由标记。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))


def _load():
    from legion_command import zhaoyun_wargame as zw
    return zw


def _fake(payload):
    return lambda cmd, **kw: SimpleNamespace(returncode=0,
                                             stdout=json.dumps(payload),
                                             stderr="")


def test_wargame_view_ok():
    zw = _load()
    rep = zw.wargame_view("加息周期", runner=_fake({"ok": True, "kind": "wargame_v0",
                                                    "scenarios": []}))
    assert rep["ok"] is True and rep["view"] == "wargame"


def test_wargame_view_empty_question():
    zw = _load()
    rep = zw.wargame_view("   ")
    assert rep["ok"] is False and rep["error"] == "empty_question"


def test_wargame_view_bad_json():
    zw = _load()
    rep = zw.wargame_view("x", runner=lambda c, **k: SimpleNamespace(
        returncode=0, stdout="notjson", stderr=""))
    assert rep["ok"] is False and rep["error"] == "bad_json"


def test_wargame_view_runner_error():
    zw = _load()
    def boom(cmd, **kw):
        raise OSError("no py")
    rep = zw.wargame_view("x", runner=boom)
    assert rep["ok"] is False and rep["error"] == "wargame_call_failed"


def test_page_and_route_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "zhaoyun.html").read_text(encoding="utf-8")
    s = (WT / "agent_platform" / "legion_command" / "serve.py").read_text(encoding="utf-8")
    assert "兵棋推演" in h and "wargameRun" in h and "wg-question" in h
    assert 'sub == "wargame"' in s
