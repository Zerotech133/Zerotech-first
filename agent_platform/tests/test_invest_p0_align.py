# -*- coding: utf-8 -*-
"""量化交易部 P0 对齐测试（2026-10-29 WO-INV-P0-ALIGN）。

锁死：
1. D1 模型绑定 org/team 双源一致（glm/glm-5.3）；
2. strategy 团队可加载（ST1 已接入）；
3. ST1↔alpha 接口文档存在；
4. QS1 运行探针：无事件=standby、有 ok 心跳=active、能读 doing 单；
5. 心跳配置注明 QS1 运行探针。
"""
import importlib.util
import json
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
REPO = PLATFORM.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

from interpreter import Team  # noqa: E402

ORG = json.loads((PLATFORM / "registry" / "org.json").read_text(encoding="utf-8"))
TEAM_INVEST = json.loads((PLATFORM / "registry" / "team_invest.json").read_text(encoding="utf-8"))


def _org_d1():
    for d in ORG["departments"]:
        if d.get("dept") == "invest":
            for p in d.get("agent_profiles") or []:
                if p.get("agent_id") == "D1":
                    return p
    return {}


def _team_d1():
    for a in TEAM_INVEST["agents"]:
        if a.get("agent_id") == "D1":
            return a
    return {}


def test_d1_model_binding_unified():
    assert _org_d1().get("llm_binding") == "glm/glm-5.3"
    assert _team_d1().get("llm_binding") == "glm/glm-5.3"


def test_strategy_team_loads_st1():
    t = Team(PLATFORM / "registry" / "team_quant_strategy.json")
    assert "st1_strategy" in t.by_node
    assert t.by_agent["ST1"]["llm_binding"] == "glm/glm-5.3"


def test_strategy_alpha_interface_doc():
    p = REPO / "docs" / "specs" / "2026-10-29_策略线与alpha接口_v1.md"
    text = p.read_text(encoding="utf-8")
    assert "team_quant_strategy" in text and "team_invest" in text
    assert "draft≠adopt" in text or "draft" in text


def _load_probe():
    spec = importlib.util.spec_from_file_location("qs1_probe", TOOLS / "qs1_runtime_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qs1_probe_standby_and_active():
    mod = _load_probe()
    now = __import__("datetime").datetime(2026, 10, 29, 23, 0, 0)
    tasks = {"WO-INV-WEEK-20261029-QS1": {"owner": "QS1", "status": "doing"}}
    r = mod.probe([], tasks, now=now, days=7)
    assert r["status"] == "standby" and r["doing_tasks"] == ["WO-INV-WEEK-20261029-QS1"]
    events = [{"ts": "2026-10-29 09:20:00", "kind": "patrol_verdict", "agent": "QS1",
               "detail": "seat_heartbeat ok=True"}]
    r2 = mod.probe(events, tasks, now=now, days=7)
    assert r2["status"] == "active"


def test_heartbeat_config_points_qs1_probe():
    cfg = json.loads((PLATFORM / "config" / "seat_heartbeat_seats.json").read_text(encoding="utf-8"))
    assert "qs1_runtime_probe.py" in cfg.get("_runtime_note", "")
