# -*- coding: utf-8 -*-
"""P2 兵棋单测（确定性，不联网）。"""
import importlib.util
import json
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("zhuge_wargame",
                                                  _TOOLS / "zhuge_wargame.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wargame_three_scenarios():
    w = _load()
    rep = w.wargame("加息周期下是否加仓成长股")
    assert rep["ok"] is True
    assert [s["name"] for s in rep["scenarios"]] == ["保守", "基准", "积极"]
    assert rep["scenarios"][1]["impact_score"] == 0.0
    assert rep["read_only"] is True and rep["executable"] is False


def test_wargame_direction_signs():
    w = _load()
    vars_ = {"利率": {"base": 3, "down": 2, "up": 4, "weight": 0.5}}
    rep = w.wargame("利率上行", vars_)
    scores = {s["name"]: s["impact_score"] for s in rep["scenarios"]}
    assert scores["保守"] > scores["基准"]  # 利率下行对成长股有利（score 正向）
    assert scores["积极"] < scores["基准"]


def test_wargame_sensitivity_sorted():
    w = _load()
    rep = w.wargame("政策与需求")
    contribs = [x["contribution"] for x in rep["sensitivity"]]
    assert contribs == sorted(contribs, reverse=True)
    assert all(x["variable"] for x in rep["sensitivity"])


def test_wargame_triggers_present():
    w = _load()
    rep = w.wargame("利率与需求")
    assert rep["triggers"] and all("→" in t for t in rep["triggers"])


def test_wargame_empty_question():
    w = _load()
    rep = w.wargame("   ")
    assert rep["ok"] is False and rep["error"] == "empty_question"


def test_wargame_cli_json(capsys):
    w = _load()
    rc = w.main(["--question", "利率走势", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0 and data["ok"] is True and data["kind"] == "wargame_v0"


def test_wargame_markdown():
    w = _load()
    md = w.to_markdown(w.wargame("需求与政策"))
    assert "情景" in md and "敏感性" in md and "触发条件" in md
