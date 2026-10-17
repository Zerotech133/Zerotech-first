"""Tests for the read-only source adapter."""

import json
from pathlib import Path

try:
    from .repository import RegistryReader
except ImportError:
    from repository import RegistryReader


def make_sources(tmp_path: Path) -> RegistryReader:
    org_path = tmp_path / "org.json"
    org_path.write_text(json.dumps({
        "org": "test",
        "host": "GenOS",
        "report_to": "human",
        "chief": {"agent_id": "A0"},
        "departments": [{"dept": "ops_runtime", "name": "运行体部", "lead": "A0A", "agents": ["WATCH_TOWER"], "agent_profiles": [{"agent_id": "WATCH_TOWER", "role_profile": "watcher", "grade": "E2"}]}],
    }), encoding="utf-8")
    hr = tmp_path / "hr" / "WATCH_TOWER"
    hr.mkdir(parents=True, exist_ok=True)
    (hr / "resume.yaml").write_text("agent_id: WATCH_TOWER\nname: 盯盘中台值守 Agent\nkind: runtime\nskills: [多方法仲裁]\n", encoding="utf-8")
    (hr / "appointment.yaml").write_text("agent_id: WATCH_TOWER\ndept: ops_runtime\ntitle: 盯盘值守官\nrank: E2\nsupervisor: AgentWatchdog\nendpoint: 127.0.0.1:9106\n", encoding="utf-8")
    return RegistryReader(org_path, tmp_path / "hr")


def test_tree_and_detail_are_source_backed(tmp_path: Path) -> None:
    reader = make_sources(tmp_path)
    tree = reader.organization_tree()
    assert tree["children"][0]["children"][0]["agent_id"] == "WATCH_TOWER"
    detail = reader.agent_detail("WATCH_TOWER")
    assert detail["title"] == "盯盘值守官"
    assert detail["supervisor_id"] == "AgentWatchdog"
    assert detail["endpoint"] == "127.0.0.1:9106"
    assert reader.health("WATCH_TOWER")["reachable"] is False
    assert reader.health("WATCH_TOWER")["quality"] == "probe_failed"


def test_tcp_fallback_is_degraded_not_online(tmp_path: Path) -> None:
    reader = make_sources(tmp_path)
    result = reader._probe("WATCH_TOWER", "127.0.0.1:1", None)
    assert result["state"] == "offline"
    assert result["quality"] == "probe_failed"


def test_http_non_json_is_probe_failed(tmp_path: Path) -> None:
    reader = make_sources(tmp_path)
    result = reader._probe("WATCH_TOWER", "http://127.0.0.1:1", None)
    assert result["state"] == "offline"
    assert result["quality"] == "probe_failed"


def test_unknown_agent_is_rejected(tmp_path: Path) -> None:
    reader = make_sources(tmp_path)
    try:
        reader.agent_detail("MISSING")
    except KeyError:
        return
    raise AssertionError("missing agent must raise KeyError")
