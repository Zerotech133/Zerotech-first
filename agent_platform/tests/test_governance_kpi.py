# -*- coding: utf-8 -*-
"""治理 KPI 聚合单测：board/health/gate/bandit/OSV/deadlines（注入目录，无网络）。"""
import datetime as dt
import json
import sys
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from legion_command import governance_kpi as gk  # noqa: E402


def _write_json(p: Path, obj, bom=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=1)
    p.write_text(data, encoding="utf-8-sig" if bom else "utf-8")


def test_collect_with_injected_sources(tmp_path):
    repo = tmp_path / "repo"
    _write_json(repo / ".worktrees" / "tasks.json",
                {"t1": {"status": "done"}, "t2": {"status": "doing"}})
    _write_json(repo / "tools" / "zhaoyun_deadlines.json", {"items": [
        {"id": "A", "title": "过期", "due": "2026-10-29T09:00:00+08:00"},
        {"id": "B", "title": "明天", "due": "2026-10-31T09:00:00+08:00"},
        {"id": "C", "title": "日常", "recurring": "daily@18:30"},
    ]})
    tmp = tmp_path / "tmp"
    _write_json(tmp / "security_audit_status.json",
                {"bandit_high": 0, "bandit_medium": 4, "bandit_low": 1368,
                 "bandit_regression": False}, bom=True)
    _write_json(tmp / "pip_osv_report.json",
                {"scanned": 308, "vulnerable_packages": 1, "vulnerabilities": 13})
    log = tmp / "gate_after_now.log"
    log.write_bytes("GATE PASS: 无新增失败".encode("utf-16"))

    now = dt.datetime(2026, 10, 30, 18, 0, 0)
    kpi = gk.collect(repo=repo, tmp=tmp, now=now,
                     health={"8796": True, "8091": True, "6006": False})
    assert kpi["ok"] is True
    assert kpi["board"]["total"] == 2
    assert kpi["board"]["by_status"] == {"done": 1, "doing": 1}
    assert kpi["health"]["6006"] is False
    assert kpi["gate"] == "PASS"
    assert kpi["bandit"]["medium"] == 4
    assert kpi["osv"]["vulns"] == 13
    states = {x["id"]: x["state"] for x in kpi["deadlines"]}
    assert states["A"] == "overdue" and states["B"] == "due_soon" and states["C"] == "recurring"


def test_gate_unknown_when_no_log(tmp_path):
    assert gk._gate_status(tmp_path) == "UNKNOWN"


def test_collect_missing_sources_is_fail_soft(tmp_path):
    kpi = gk.collect(repo=tmp_path, tmp=tmp_path,
                     now=dt.datetime(2026, 10, 30, 18, 0, 0),
                     health={"8796": False})
    assert kpi["ok"] is True
    assert kpi["board"]["total"] == 0
    assert kpi["bandit"] is None and kpi["osv"] is None
    assert kpi["worktrees"] is None
