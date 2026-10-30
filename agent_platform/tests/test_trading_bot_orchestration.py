# -*- coding: utf-8 -*-
"""交易机器人多角色组编排测试（WO-TRADING-BOT-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
REG = REPO / "agent_platform" / "registry" / "trading_bot.json"
ORG = REPO / "agent_platform" / "registry" / "org.json"
PROJECTS = REPO / "agent_platform" / "registry" / "projects.json"


def _j(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_roles_and_order():
    d = _j(REG)
    assert d["owner"] == "D1" and d["manager"] == "TR1"
    ids = [s["id"] for s in d["stages"]]
    assert ids == ["select", "research", "plan", "watch", "human_confirm", "execute", "review"]
    assert d["status"] == "paper_only" and d["trading_permitted"] is False


def test_roles_exist_in_org():
    d = _j(REG)
    org = _j(ORG)
    agents = {a for dep in org["departments"] for a in dep.get("agents", [])}
    used = set()
    for s in d["stages"]:
        if s["owner"] != "HUMAN":
            used.add(s["owner"])
        used.update(s.get("members") or [])
    assert used <= agents, used - agents
    assert "WA1" in agents and "TR1" in agents


def test_watch_layer_exists():
    d = _j(REG)
    assert d["watch_layer"]["business_role"].startswith("WA1")
    assert "WATCH_TOWER" in d["watch_layer"]["middle_platform"]
    projects = {p["project_id"]: p for p in _j(PROJECTS)["projects"]}
    assert d["watch_layer"]["engineering_project"].startswith("watchbot")
    assert "watchbot" in projects
    # 2026-10-30 检查结论：三层均为离线契约/仅登记，实时入口未启用
    assert projects["watchbot"]["runtime_status"] == "offline_contract"
    assert projects["watchbot"]["live_entry"] is False
    assert d["watch_runtime"]["enabled_live"] is False


def test_human_gate_present():
    d = _j(REG)
    assert d["stages"][4]["id"] == "human_confirm"
    assert d["stages"][4]["human_gate"] is True
    assert "BUY" in d["human_interface"]["required_for"]
    assert d["human_interface"]["kill_switch"]
