# -*- coding: utf-8 -*-
"""P2 三席辩论单测（确定性，不联网）。"""
import importlib.util
import json
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("zhuge_debate",
                                                  _TOOLS / "zhuge_debate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_three_seats_and_fields():
    d = _load()
    rep = d.debate("加息周期下是否加仓成长股")
    assert rep["ok"] is True and rep["kind"] == "debate_v0"
    assert [s["seat"] for s in rep["seats"]] == ["QJ1", "WJ1", "FK1"]
    for s in rep["seats"]:
        for k in ("stance", "argument", "evidence", "risk", "confidence",
                  "change_mind"):
            assert s[k]


def test_divergence_has_dissent():
    d = _load()
    rep = d.debate("加息周期下是否加仓成长股")
    div = rep["divergence"]
    assert div["dissent"], "应有结构化分歧"
    assert rep["decision_hint"]


def test_shared_basis_from_wargame():
    d = _load()
    rep = d.debate("利率与需求变化")
    basis = rep["shared_basis"]
    assert basis["source"] == "zhuge_wargame 规则推演"
    assert "保守" in basis["scenarios"] and "积极" in basis["scenarios"]
    assert basis["top_variable"]


def test_empty_question_fail():
    d = _load()
    rep = d.debate("   ")
    assert rep["ok"] is False and rep["error"] == "empty_question"


def test_readonly_no_execution():
    d = _load()
    rep = d.debate("政策变化")
    assert rep["read_only"] is True and rep["executable"] is False


def test_cli_json(capsys):
    d = _load()
    rc = d.main(["--question", "是否加仓", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True


def test_markdown_sections():
    d = _load()
    md = d.to_markdown(d.debate("需求与政策"))
    assert "席位" in md and "分歧" in md and "决策提示" in md
