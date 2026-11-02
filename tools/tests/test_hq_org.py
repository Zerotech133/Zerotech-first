# -*- coding: utf-8 -*-
"""HQ-01 组织/角色/权限模型：真实仓库可读、结构完整、JSON 纯。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_org  # noqa: E402


def test_org_shape():
    o = hq_org.build_org()
    assert o["chief"]["agent_id"] == "A0"
    assert o["summary"]["departments"] >= 17
    assert o["summary"]["agents"] >= 77
    assert all(d["name"] for d in o["departments"])
    assert all(d["lead"] for d in o["departments"] if d["dept"] != "trading")


def test_agents_have_department_and_name():
    o = hq_org.build_org()
    agents = [a for d in o["departments"] for a in d["agents"]]
    assert len(agents) == o["summary"]["agents"]
    assert all(a["agent_id"] and a["dept"] and a["name"] for a in agents)
    assert any(a["is_lead"] for a in agents)


def test_gaps_are_lists():
    o = hq_org.build_org()
    assert isinstance(o["gaps"]["no_hr_profile"], list)
    assert isinstance(o["gaps"]["no_team_projection"], list)


def test_json_cli_pure(capsys):
    rc = hq_org.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["summary"]["agents"] >= 77
    assert d["chief"]["agent_id"] == "A0"
