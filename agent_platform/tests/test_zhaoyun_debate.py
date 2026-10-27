# -*- coding: utf-8 -*-
"""辩论接线单测。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))


def _load():
    from legion_command import zhaoyun_debate as zd
    return zd


def _fake(payload):
    return lambda cmd, **kw: SimpleNamespace(returncode=0,
                                             stdout=json.dumps(payload), stderr="")


def test_debate_view_ok():
    zd = _load()
    rep = zd.debate_view("加息周期", runner=_fake({"ok": True, "kind": "debate_v0"}))
    assert rep["ok"] is True and rep["view"] == "debate"


def test_debate_view_empty():
    zd = _load()
    rep = zd.debate_view("  ")
    assert rep["ok"] is False and rep["error"] == "empty_question"


def test_debate_view_bad_json():
    zd = _load()
    rep = zd.debate_view("x", runner=lambda c, **k: SimpleNamespace(
        returncode=0, stdout="notjson", stderr=""))
    assert rep["ok"] is False and rep["error"] == "bad_json"


def test_page_and_route_markers():
    h = (WT / "agent_platform" / "legion_command" / "static" / "zhaoyun.html").read_text(encoding="utf-8")
    s = (WT / "agent_platform" / "legion_command" / "serve.py").read_text(encoding="utf-8")
    assert "多智能体辩论" in h and "debateRun" in h and "db-question" in h
    assert 'sub == "debate"' in s
