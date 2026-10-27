# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_evolver as E  # noqa: E402


def test_quality_failed_counted_and_s5(tmp_path, monkeypatch):
    missions = tmp_path / "missions"
    monkeypatch.setattr(E, "MISSIONS", missions)
    m = missions / "M-Q"
    m.mkdir(parents=True)
    (m / "plan.json").write_text(json.dumps({"mission_id": "M-Q", "name": "q", "source": "manual-demo"}), encoding="utf-8")
    (m / "dry_run.json").write_text(json.dumps({"conflicts": []}), encoding="utf-8")
    (m / "state.json").write_text(json.dumps({
        "mission_id": "M-Q", "tasks": {
            "T1": {"owner": "DEV5", "status": "done", "attempts": 1},
            "T2": {"owner": "DEV5", "status": "quality_failed", "attempts": 1},
        }}), encoding="utf-8")
    recs = E.collect()
    assert recs[0]["tasks_quality_failed"] == 1
    m = E.summarize(recs)
    assert m["quality_failed"] == 1 and m["gate_pass_rate"] == 0.5
    sugg = E.suggestions(m)
    assert any(s["id"] == "S5" and s.get("rule", {}).get("require_checks") for s in sugg)
